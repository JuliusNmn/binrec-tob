#include <cassert>

#include <s2e/S2E.h>
#include <s2e/S2EExecutor.h>
#include <s2e/Plugins/ModuleExecutionDetector.h>

#include <llvm/LLVMContext.h>
#include <llvm/Function.h>
#include <llvm/Metadata.h>
#include <llvm/Constants.h>
#include <llvm/Bitcode/ReaderWriter.h>

#include "ModuleSelector.h"
#include "BBExport.h"
#include "util.h"

namespace s2e {
namespace plugins {

using namespace llvm;
using namespace klee;

S2E_DEFINE_PLUGIN(BBExport,
                  "Exports LLVM bitcode for an ELF binary",
                  "BBExport", "ModuleSelector");

#ifdef TARGET_I386

void BBExport::initialize()
{
    std::string pred = s2e()->getConfig()->getString(getConfigKey() + ".predecessor");
    std::stringstream ss;
    ss << std::hex << pred;
    ss >> m_pred;
    s2e()->getWarningsStream() << "m_pred: " << m_pred << "\n";

    std::string address = s2e()->getConfig()->getString(getConfigKey() + ".address");
    ss.clear();
    ss << std::hex << address;
    ss >> m_address;
    s2e()->getWarningsStream() << "m_address: " << m_address << "\n";

    if (!m_pred || !m_address) {
        s2e()->getWarningsStream() << "[BBExport] no address specified to export\n";
        exit(1);
    }

    ModuleSelector *selector =
        (ModuleSelector*)(s2e()->getPlugin("ModuleSelector"));
    selector->onModuleLoad.connect(
            sigc::mem_fun(*this, &BBExport::slotModuleLoad));
    selector->onModuleExecute.connect(
            sigc::mem_fun(*this, &BBExport::slotModuleExecute));

    ModuleExecutionDetector *modEx =
        (ModuleExecutionDetector*)(s2e()->getPlugin("ModuleExecutionDetector"));
    modEx->onModuleSignal.connect(
            sigc::mem_fun(*this, &BBExport::slotModuleSignal));

    s2e()->getDebugStream() << "[BBExport] Plugin initialized\n";

    std::string succsPath = s2e()->getConfig()->getString(getConfigKey() + ".prevSuccs");
    std::string symbolsPath = s2e()->getConfig()->getString(getConfigKey() + ".symbols");
    if(symbolsPath.empty() || succsPath.empty()){
        s2e()->getWarningsStream() << "prevSuccs or symbols is empty. Early termination is disabled!\n";
        m_earlyTerminate = false;
    }else{
        m_earlyTerminate = true;
        readPrevSuccs(succsPath);
        readSymbols(symbolsPath);
    }

    m_moduleLoaded = false;
    m_executionDiverted = false;
    doExport = true;
    m_llvmModule = NULL;
    m_suspend = false;
}

void BBExport::readSymbols(std::string &path){
    s2e()->getMessagesStream() << "symbols path: " << path << "\n";

    std::ifstream f;
    f.open(path.c_str());

    if (!f.is_open()){
        s2e()->getMessagesStream() << "Cannot open symbols file: " << path << "\n";
        m_earlyTerminate = false;
    }

    uint32_t addr;
    std::string symbol;
    while (f >> std::hex >> addr >> symbol) {
        symbolsSet.insert(addr);
    }
}
void BBExport::readPrevSuccs(std::string &path){
    s2e()->getMessagesStream() << "prevSuccs path: " << path << "\n";

    std::ifstream f;
    f.open(path.c_str());

    if (!f.is_open()){
        s2e()->getMessagesStream() << "Cannot open prevSuccs file: " << path << "\n";
        m_earlyTerminate = false;
    }

    uint32_t pred, succ;
    while (f.read((char*)&succ, 4).read((char*)&pred, 4)) {
        prevSuccsKeys.insert(succ);
    }
}

BBExport::~BBExport()
{
   saveLLVMModule(); 
}

void BBExport::saveLLVMModule()
{
    std::string dir = s2e()->getOutputFilename("/");
    std::string error;

    s2e()->getMessagesStream() << "Saving LLVM module... ";

    if (!m_llvmModule) {
        s2e()->getWarningsStream() << "error: module is uninitialized\n";
        return;
    }

    raw_fd_ostream bitcodeOstream((dir + "captured" + ".bc").c_str(), error, 0);
    WriteBitcodeToFile(m_llvmModule, bitcodeOstream);
    bitcodeOstream.close();

//#if WRITE_LLVM_SRC
//    if (!intermediate) {
        raw_fd_ostream llvmOstream((dir + "captured" + ".ll").c_str(), error, 0);
        llvmOstream << *m_llvmModule;
        llvmOstream.close();
//    }
//#endif

    s2e()->getMessagesStream() << "saving successor lists... ";

    raw_fd_ostream succsOstream(s2e()->getOutputFilename("succs.dat").c_str(), error, 0);
    foreach2(it, m_succs.begin(), m_succs.end()) {
        const uint64_t key = *it;
        succsOstream.write((const char*)&key, sizeof (uint64_t));
    }
    succsOstream.close();

    s2e()->getMessagesStream() << "done\n";
}


void BBExport::slotModuleSignal(S2EExecutionState *state, const ModuleExecutionCfg &desc)
{
    if (desc.moduleName == "init_env.so") {
        s2e()->getMessagesStream(state) << "stopped exporting LLVM code\n";
        doExport = false;
    }
}

void BBExport::slotModuleExecute(S2EExecutionState *state, uint64_t pc)
{   
    s2e()->getMessagesStream(state) << "execute " << hexval(pc) << "\n";
    
    if (m_currentBlock && doExport) {
        s2e()->getMessagesStream() << "execute: collecting llvm\n";
        //slotModuleExecuteBlock(state, pc);
        addEdge(m_pred, pc);
        if (!m_llvmModule){
            Function *f = state->getTb()->llvm_function;
            assert(f && "no llvm function in tb");
            m_llvmModule = f->getParent();
        }
        //plt code tricks us to think that we run into tb from previous lifting. Using symbols and 6byte difference for additional info works pretty well.
        if(m_earlyTerminate){
            if(prevSuccsKeys.find(pc) != prevSuccsKeys.end() && symbolsSet.find(pc) == symbolsSet.end() && pc - m_pred != 6){
                s2e()->getMessagesStream(state) << "Hit previously lifted tb. Lifting is stoping..\n";
                if(m_suspend) return;
                m_suspend = true;
                //m_tbStartConnection = s2e()->getCorePlugin()->onTranslateBlockStart.connect(
                //        sigc::mem_fun(*this, &BBExport::suspend));
                g_s2e->getExecutor()->suspendState(state);
            }
        }
        m_pred = pc;
    }
/*
    if (plgState->doExport) {
        exportBB(state, pc);
        addSuccessor(plgState->prevPc, pc);
    }

    plgState->prevPc = pc;
*/
}



void BBExport::addEdge(uint64_t predPc, uint64_t succ){
    
    s2e()->getMessagesStream() << "pred: " << hexval(predPc) << "succ: " << hexval(succ) << '\n';
    m_succs.insert((predPc << 32) | (succ & 0xffffffff));
}
 
void BBExport::slotModuleLoad(S2EExecutionState *state,
                              const ModuleDescriptor &module)
{
    if (!module.Contains(m_address)) {
        s2e()->getWarningsStream() << "[BBExport] module " << module.Name
            << " (" << hexval(module.LoadBase) << "" <<
            hexval(module.LoadBase + module.Size) <<
            ") does not contain address " << m_address << "\n";
        exit(1);
    }

    m_module = module;
    m_moduleLoaded = true;

    m_tbStartConnection = s2e()->getCorePlugin()->onTranslateBlockStart.connect(
            sigc::mem_fun(*this, &BBExport::slotTranslateBlockStart));
    m_tbEndConnection = s2e()->getCorePlugin()->onTranslateBlockEnd.connect(
            sigc::mem_fun(*this, &BBExport::slotTranslateBlockEnd));
}

void BBExport::suspend(
    ExecutionSignal *signal,
    S2EExecutionState *state,
    TranslationBlock *tb,
    uint64_t pc)
{
    s2e()->getMessagesStream() << "suspending..\n";
    g_s2e->getExecutor()->suspendState(state);
}

void BBExport::slotTranslateBlockStart(
    ExecutionSignal *signal,
    S2EExecutionState *state,
    TranslationBlock *tb,
    uint64_t pc)
{
    s2e()->getMessagesStream() << "blockstart\n";
    //if(m_suspend){
    //    m_startSignal->connect(sigc::mem_fun(*this, &BBExport::slotModuleExecuteBlock));
    if (m_moduleLoaded && pc == m_address) {
        s2e()->getMessagesStream() << "blockstart: set start signal\n";
        m_currentBlock = pc;
        //m_startSignal = signal;
        m_tbStartConnection.disconnect();
        m_tbEndConnection.disconnect();
        
/*        if (!m_llvmModule){
            Function *f = state->getTb()->llvm_function;
            assert(f && "no llvm function in tb");
            m_llvmModule = f->getParent();
        }
*/    }
}

void BBExport::slotTranslateBlockEnd(
    ExecutionSignal *signal,
    S2EExecutionState *state,
    TranslationBlock *tb,
    uint64_t pc,
    bool staticTargetValid,
    uint64_t staticTarget)
{
    s2e()->getMessagesStream() << "blockend\n";
    if (!m_currentBlock) {
        if (m_moduleLoaded && !m_executionDiverted && m_module.Contains(pc)) {
            s2e()->getMessagesStream() << "blockend: connect executefirst\n";
            signal->connect(sigc::mem_fun(*this,
                        &BBExport::slotModuleExecuteFirst));
            //m_tbEndConnection.disconnect();
        }
    } 
    /*else {
        s2e()->getMessagesStream(state) <<
            "translateblockstart: pc=" << hexval(pc) <<
            ", cur=" << hexval(m_currentBlock) << "\n";

        if (pc == m_currentBlock) {
            s2e()->getMessagesStream(state) << "ignoring empty block\n";
        } else {
            //m_startSignal->connect(sigc::mem_fun(*this,
            //            &BBExport::slotModuleExecuteBlock));
        }

        m_currentBlock = 0;
    }*/
}

void BBExport::slotModuleExecuteFirst(S2EExecutionState* state, uint64_t pc)
{
    if (!m_executionDiverted) {
        s2e()->getMessagesStream(state) <<
            "executed first block, divert execution\n";
        state->setPc(m_address);
        m_executionDiverted = true;
    }
}

void BBExport::slotModuleExecuteBlock(S2EExecutionState* state, uint64_t pc)
{
    Function *f = state->getTb()->llvm_function;
    assert(f && "no llvm function in tb");

    /*std::string succsFilename = s2e()->getOutputFilename("succs.dat"), succsError;
    raw_fd_ostream succsOstream(succsFilename.c_str(), succsError, 0);

    if (succsOstream.has_error()) {
        s2e()->getWarningsStream(state) <<
            "failed to open succs file " << succsFilename << "\n";
        exit(1);
    }
    
    uint64_t edge = (m_pred << 32) | (m_address & 0xffffffff);
    succsOstream.write((const char*)&edge, sizeof (uint64_t));
    extractSuccEdge(*f); 

    succsOstream.close();
*/

    std::stringstream ss;
    ss << "BB_" << hexval(m_address) << ".ll";
    std::string filename = s2e()->getOutputFilename(ss.str()), error;
    raw_fd_ostream ostream(filename.c_str(), error, 0);

    if (ostream.has_error()) {
        s2e()->getWarningsStream(state) <<
            "failed to open outfile " << filename << "\n";
        exit(1);
    }

    //f->setName("Func_" + f->getName().substr(f->getName().rfind('-') + 1));
    ostream << *(f->getParent());
    
    s2e()->getMessagesStream(state) <<
        "exported BB at PC " << hexval(m_address) << "\n";
    saveLLVMModule(*f);
    
    //g_s2e->getExecutor()->suspendState(state);
}

void BBExport::saveLLVMModule(Function &F)
{
    std::string dir = s2e()->getOutputFilename("/");
    std::string error;

    s2e()->getMessagesStream() << "Saving LLVM module... ";

    raw_fd_ostream bitcodeOstream((dir + "captured" + ".bc").c_str(), error, 0);
    WriteBitcodeToFile(F.getParent(), bitcodeOstream);
    bitcodeOstream.close();
    
    s2e()->getMessagesStream() << "done\n";
}

uint64_t BBExport::extractSuccEdge(Function &F){
   //Here we can get successor of F if it is direct branch.
    return 0;
}

#else // TARGET_I386

void BBExport::initialize()
{
    s2e()->getWarningsStream() << "[BBExport] This plugin is only suited for i386\n";
}

#endif

} // namespace plugins
} // namespace s2e
