#define DEBUG_TYPE "section-utils"
#include <fstream>

#include "SectionUtils.h"
#include "PassUtils.h"

#define WRAPPER_SECTION ".wrapper"

using namespace llvm;

StringRef getSourcePath(Module &m) {
    return s2eOutFile("binary");
}

void writeSectionConfig(StringRef name, size_t loadBase) {
    outs() << "$(BIN): LDFLAGS += --section-start=" << name << "=0x";
    outs().write_hex(loadBase);
    outs() << '\n';
}

void copySection(Module &m, section_meta_t &s, uint8_t *data, bool readonly) {
    DEBUG(dbgs() << "copy section at 0x" << intToHex(s.loadBase) << ": ");
    DEBUG(dbgs() << s.name << "\t(" << s.size << " bytes ");
    LLVMContext &ctx = m.getContext();
    Constant *initializer;

    if (data) {
        // copy section contents to initializer
        DEBUG(dbgs() << "raw data");
        ArrayRef<uint8_t> adata(data, s.size);
        initializer = ConstantDataArray::get(ctx, adata);
    } else {
        // section has no contents, set zero initializer
        DEBUG(dbgs() << "zero-initialized");
        Type *bytety = Type::getInt8Ty(ctx);
        ArrayType *arrayty = ArrayType::get(bytety, s.size);
        initializer = ConstantAggregateZero::get(arrayty);
    }

    // create global
    assert(m.getGlobalList().size());
    static GlobalVariable *insertBefore = &*(m.getGlobalList().begin());
    s.global = new GlobalVariable(m, initializer->getType(),
            readonly, GlobalValue::ExternalLinkage, initializer,
            s.name, insertBefore);
    s.global->setSection(s.name);
    s.global->setAlignment(1);

    if (readonly) DEBUG(dbgs() << ", readonly");
    DEBUG(dbgs() << ")\n");
}

void readSectionMeta(section_meta_t &s, MDNode *md) {
    s.md = md;
    s.name = cast<MDString>(md->getOperand(0))->getString();
    s.loadBase = cast<ConstantInt>(dyn_cast<ValueAsMetadata>(md->getOperand(1))->getValue())->getZExtValue();
    s.size = cast<ConstantInt>(dyn_cast<ValueAsMetadata>(md->getOperand(2))->getValue())->getZExtValue();
    s.fileOffset = cast<ConstantInt>(dyn_cast<ValueAsMetadata>(md->getOperand(3))->getValue())->getZExtValue();
    Value *V = dyn_cast<ValueAsMetadata>(md->getOperand(4))->getValue();
    s.global = (GlobalVariable*)V;
}

void writeSectionMeta(Module &m, section_meta_t &s) {
    LLVMContext &ctx = m.getContext();
    NamedMDNode *secs = m.getOrInsertNamedMetadata(SECTIONS_METADATA);

#define NOPERANDS 5
    Metadata *operands[NOPERANDS] = {
        MDString::get(ctx, s.name),
        ConstantAsMetadata::get(ConstantInt::get(Type::getInt32Ty(ctx), s.loadBase)),
        ConstantAsMetadata::get(ConstantInt::get(Type::getInt32Ty(ctx), s.size)),
        ConstantAsMetadata::get(ConstantInt::get(Type::getInt32Ty(ctx), s.fileOffset)),
        ValueAsMetadata::get(s.global)
    };

    if (s.md) {
        for (size_t i = 0; i < NOPERANDS; i++)
            s.md->replaceOperandWith(i, operands[i]);
    } else {
        ArrayRef<Metadata*> opRef(operands);
        s.md = MDNode::get(ctx, opRef);
        secs->addOperand(s.md);
    }
#undef NOPERANDS
}

bool mapToSections(Module &m, section_map_fn_t fn, void *param) {
    NamedMDNode *secs = m.getOrInsertNamedMetadata(SECTIONS_METADATA);
    section_meta_t s;

    for (unsigned i = 0, l = secs->getNumOperands(); i < l; i++) {
        readSectionMeta(s, secs->getOperand(i));

        if (fn(s, param))
            return true;
    }

    return false;
}

bool readFromBinary(Module &m, void *buf, unsigned offset, unsigned size) {
    std::ifstream infile(getSourcePath(m).data(), std::ifstream::binary);
    infile.seekg(offset, infile.beg);
    infile.read((char*)buf, size);
    bool success = (bool)infile;
    infile.close();
    return success;
}

bool findSectionByName(Module &m, const std::string name, section_meta_t &s) {
    NamedMDNode *secs = m.getOrInsertNamedMetadata(SECTIONS_METADATA);

    for (unsigned i = 0, l = secs->getNumOperands(); i < l; i++) {
        readSectionMeta(s, secs->getOperand(i));

        if (s.name == name)
            return true;
    }

    return false;
}
