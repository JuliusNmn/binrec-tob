#ifndef BINREC_UNTANGLE_INTERPRETER_HPP
#define BINREC_UNTANGLE_INTERPRETER_HPP

#include <llvm/Analysis/LoopPass.h>
#include <llvm/Support/CommandLine.h>
#include <map>

using namespace llvm;

extern cl::opt<std::string> InterpreterEntry;

extern cl::opt<std::string> VpcGlobal;

extern cl::list<std::string> VpcTraceFiles;

using HeaderLatchPair = std::pair<BasicBlock *, BasicBlock *>;

struct UntangleInterpreter : public LoopPass {
    static char ID;
    UntangleInterpreter() : LoopPass(ID) {}

    auto runOnLoop(Loop *L, LPPassManager &LPM) -> bool override;
    void getAnalysisUsage(AnalysisUsage &AU) const override;

private:
    GlobalVariable *vpc{};
    Loop *interpreter{};
    std::map<unsigned, HeaderLatchPair> vpcMap;
    BasicBlock *errBlock{nullptr};

    auto getOrCreatePair(const unsigned vpcValue) -> const HeaderLatchPair &;
};

#endif
