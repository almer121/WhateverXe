#!/usr/bin/env python3
"""Check opt-in GPU_TICKS suppression at the production native-read boundary.

Basis: Elezorn 804a0a0 returns zero (no sample) before GPU_TICKS hardware access.
Execute processKernel's selection/argument/publication prelude and the register
wrapper through its native read/trace boundary. PCI selection, registry, clock
translation and tracing are modeled leaves. Later fuse/GMBUS handling, hook
installation, kernel startup and physical GPU behavior are outside this check.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'WhateverXe/kern_xe.cpp').read_text()
kernel = source[source.index('void XE::processKernel('):]
prelude = kernel[:kernel.index('    // ADLP_LOAD_ONLY_KERNEL:')]
wrapper = source[source.index('uint32_t XE::wrapTGLRegisterRead('):]
wrapper = wrapper[:wrapper.index('\tconst auto caller =')] + '\treturn value;\n}\n'

code = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <initializer_list>
struct KernelPatcher {};
struct IOPCIDevice {};
struct DeviceInfo { IOPCIDevice *videoBuiltin; };
#define OSDynamicCast(type, value) (value)
namespace XELP { int deviceProfile(IOPCIDevice *gpu) { assert(gpu); return 1; } }
static bool enabled=false,selected=false,published=false,publishedValue=false;
static unsigned nativeReads=0,clockChecks=0,traces=0;
static void *expectedAccess=nullptr;
static unsigned long lastRegister=0;
bool checkKernelArgument(const char *name) {
 assert(selected && !strcmp(name,"-wex-skip-gputicks")); return enabled;
}
struct IORegistryEntry {
 static IORegistryEntry *getRegistryRoot() { static IORegistryEntry root; return &root; }
 bool setProperty(const char *name,bool value) {
  assert(selected && !strcmp(name,"WhateverXeSkipGPUTicks"));
  published=true;publishedValue=value;return true;
 }
};
uint32_t nativeRead(void *access,unsigned long reg) {
 assert(access==expectedAccess);++nativeReads;lastRegister=reg;return 0x89abcdefU;
}
bool comboClockAccess(void *) { ++clockChecks;return false; }
namespace XELPComboClock { uintptr_t donorPC(uintptr_t,uintptr_t) { return 0; } }
struct { struct { unsigned long reg(unsigned long r,uintptr_t) { return r; } } translation; } comboClockTransaction;
bool tglTransitionRegister(unsigned long) { return true; }
uintptr_t tglConnectionCaller(void *) { return 0; }
void tglTransitionRecord(unsigned,bool,void *,unsigned long,uintptr_t,uint32_t) { ++traces; }
struct XE {
 bool tglSkipGPUTicks=false;
 uintptr_t framebufferClockImage=0,orgTGLRegisterRead=reinterpret_cast<uintptr_t>(&nativeRead);
 void selectProfile(int profile) { assert(profile==1);selected=true; }
 void processKernel(KernelPatcher &,DeviceInfo *);
 static uint32_t wrapTGLRegisterRead(void *,unsigned long);
};
static XE *callbackXE;
#define FunctionCast(f,p) reinterpret_cast<decltype(&f)>(p)
PRELUDE
}
WRAPPER
int main() {
 IOPCIDevice gpu;DeviceInfo info{&gpu};KernelPatcher patcher;
 // Reuse one object: disabling must clear both runtime flag and publication.
 XE xe;callbackXE=&xe;expectedAccess=&gpu;assert(!xe.tglSkipGPUTicks);
 for(bool flag:{false,true,false}) {
  enabled=flag;selected=published=false;xe.processKernel(patcher,&info);
  assert(selected && published && publishedValue==flag && xe.tglSkipGPUTicks==flag);
  for(unsigned long reg:{0x2910UL,0x290cUL,0x2914UL,0x1234UL}) {
   nativeReads=clockChecks=traces=0;lastRegister=0;
   const auto value=XE::wrapTGLRegisterRead(expectedAccess,reg);
   if(flag && reg==0x2910) {
    assert(value==0 && nativeReads==0 && clockChecks==0 && traces==0);
   } else {
    assert(value==0x89abcdefU && nativeReads==1 && lastRegister==reg);
    assert(clockChecks==1 && traces==2);
   }
  }
 }
 puts("PASS GPU_TICKS opt-in: kernel publication, no native read, default and unrelated registers preserved");
}
'''.replace('PRELUDE', prelude).replace('WRAPPER', wrapper)
with tempfile.TemporaryDirectory(prefix='tgl-gpu-ticks-') as directory:
    directory = Path(directory)
    cpp, exe = directory / 'test.cpp', directory / 'test'
    cpp.write_text(code)
    subprocess.run(['clang++', '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
