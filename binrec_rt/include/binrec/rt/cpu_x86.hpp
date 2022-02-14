#ifndef BINREC_CPU_X86_HPP
#define BINREC_CPU_X86_HPP

#ifdef __cplusplus
#include <cstdint>
#else
#include <stdint.h>
#endif

#ifdef __cplusplus
extern "C" {
#endif
/* eflags masks */
#define CC_C 0x0001
#define CC_P 0x0004
#define CC_A 0x0010
#define CC_Z 0x0040
#define CC_S 0x0080
#define CC_O 0x0800

#define TF_SHIFT 8
#define IOPL_SHIFT 12
#define VM_SHIFT 17

#define TF_MASK 0x00000100
#define IF_MASK 0x00000200
#define DF_MASK 0x00000400
#define IOPL_MASK 0x00003000
#define NT_MASK 0x00004000
#define RF_MASK 0x00010000
#define VM_MASK 0x00020000
#define AC_MASK 0x00040000
#define VIF_MASK 0x00080000
#define VIP_MASK 0x00100000
#define ID_MASK 0x00200000

/* mflags - mode and control part of eflags */
#define CFLAGS_MASK (CC_O | CC_S | CC_Z | CC_A | CC_P | CC_C)
#define MFLAGS_MASK ~(CC_O | CC_S | CC_Z | CC_A | CC_P | CC_C | DF_MASK)

typedef uint32_t target_ulong;
typedef uint32_t addr_t;
typedef uint32_t stackword_t;
typedef uint32_t reg_t;
extern reg_t PC, R_EAX, R_EBX, R_ECX, R_EDX, R_ESI, R_EDI, R_EBP, R_ESP;
extern int32_t df;
extern uint32_t cc_src, cc_dst, cc_op, mflags;
const int R_GS = 5;

typedef struct SegmentCache {
    uint32_t selector;
    target_ulong base;
    uint32_t limit;
    uint32_t flags;
} SegmentCache;

typedef uint32_t float32;
typedef union {
    uint8_t _b[8];
    uint16_t _w[4];
    uint32_t _l[2];
    float32 _s[2];
    uint64_t q;
} __attribute__((aligned(8))) MMXReg;

typedef struct {
    uint64_t low;
    uint16_t high;
    uint16_t padding1;
    uint16_t padding2;
    uint16_t padding3;
} __attribute__((aligned(8))) floatx80;

typedef union {
    floatx80 d __attribute__((aligned(16)));
    MMXReg mmx;
} FPReg;

typedef uint8_t flag;

typedef struct float_status {
    signed char float_detect_tininess;
    signed char float_rounding_mode;
    uint8_t float_exception_flags;
    signed char floatx80_rounding_precision;
    /* should denormalised results go to zero and set the inexact flag? */
    flag flush_to_zero;
    /* should denormalised inputs go to zero and set the input_denormal flag? */
    flag flush_inputs_to_zero;
    flag default_nan_mode;
    /* not always used -- see snan_bit_is_one() in softfloat-specialize.h */
    flag snan_bit_is_one;
} float_status;

extern float_status fp_status;
extern unsigned int fpstt;
extern FPReg fpregs[8];
extern uint8_t fptags[8];

#define STACK_SIZE (1024 * 1024 * 16) / sizeof(stackword_t)
extern stackword_t stack[STACK_SIZE] __attribute__((aligned(16)));

struct CPUX86State;

uint8_t helper_ldb_mmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stb_mmu(CPUX86State *env, target_ulong addr, uint8_t val, int mmu_idx, void *retaddr);
uint16_t helper_ldw_mmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stw_mmu(CPUX86State *env, target_ulong addr, uint16_t val, int mmu_idx, void *retaddr);
uint32_t helper_ldl_mmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stl_mmu(CPUX86State *env, target_ulong addr, uint32_t val, int mmu_idx, void *retaddr);
uint64_t helper_ldq_mmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stq_mmu(CPUX86State *env, target_ulong addr, uint64_t val, int mmu_idx, void *retaddr);

uint8_t helper_ldb_cmmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stb_cmmu(CPUX86State *env, target_ulong addr, uint8_t val, int mmu_idx, void *retaddr);
uint16_t helper_ldw_cmmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stw_cmmu(CPUX86State *env, target_ulong addr, uint16_t val, int mmu_idx, void *retaddr);
uint32_t helper_ldl_cmmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stl_cmmu(CPUX86State *env, target_ulong addr, uint32_t val, int mmu_idx, void *retaddr);
uint64_t helper_ldq_cmmu(CPUX86State *env, target_ulong addr, int mmu_idx, void *retaddr);
void helper_stq_cmmu(CPUX86State *env, target_ulong addr, uint64_t val, int mmu_idx, void *retaddr);

#if 0
// TODO (hbrodin): Delete!
uint8_t helper_ldb_mmu(target_ulong addr, int mmu_idx);
void helper_stb_mmu(target_ulong addr, uint8_t val, int mmu_idx);
uint16_t helper_ldw_mmu(target_ulong addr, int mmu_idx);
void helper_stw_mmu(target_ulong addr, uint16_t val, int mmu_idx);
uint32_t helper_ldl_mmu(target_ulong addr, int mmu_idx);
void helper_stl_mmu(target_ulong addr, uint32_t val, int mmu_idx);
uint64_t helper_ldq_mmu(target_ulong addr, int mmu_idx);
void helper_stq_mmu(target_ulong addr, uint64_t val, int mmu_idx);

uint8_t helper_ldb_cmmu(target_ulong addr, int mmu_idx);
void helper_stb_cmmu(target_ulong addr, uint8_t val, int mmu_idx);
uint16_t helper_ldw_cmmu(target_ulong addr, int mmu_idx);
void helper_stw_cmmu(target_ulong addr, uint16_t val, int mmu_idx);
uint32_t helper_ldl_cmmu(target_ulong addr, int mmu_idx);
void helper_stl_cmmu(target_ulong addr, uint32_t val, int mmu_idx);
uint64_t helper_ldq_cmmu(target_ulong addr, int mmu_idx);
void helper_stq_cmmu(target_ulong addr, uint64_t val, int mmu_idx);
#endif
#ifdef __cplusplus
}
#endif

#endif
