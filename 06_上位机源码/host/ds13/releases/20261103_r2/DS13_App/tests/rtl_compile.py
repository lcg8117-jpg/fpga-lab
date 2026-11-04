"""Generic offline Verilator/C++ build helper; no serial or programmer access."""
from pathlib import Path
import ctypes,os,subprocess,time
TOOLCHAIN=Path(os.environ.get('DS11_VERILATOR_ROOT','D:/Codex_Scratch/ADB1_S1_ADL1_OFFLINE_BUILD_20261102_J280/work/integrated_S1/tools/verilator/ucrt64'))
VERILATOR=TOOLCHAIN/'bin/verilator_bin.exe';INCLUDE=TOOLCHAIN/'share/verilator/include'
DLL_BIN=Path(os.environ.get('DS11_DLL_BIN','D:/Codex_Scratch/SWD4_AutoTune_20261020/W1_Icarus_Portable/ucrt64/bin'))
GXX=Path(os.environ.get('DS11_GXX','D:/Users/董晨阳/Downloads/x86_64-8.1.0-release-posix-seh-rt_v6-rev0/mingw64/bin/g++.exe'))
def build(folder,top,verilog,cpp,prefix='Vsim'):
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    env=dict(os.environ);env['PATH']=str(DLL_BIN)+os.pathsep+env['PATH'];env['VERILATOR_ROOT']=str(INCLUDE.parent)
    object_dir='obj_'+str(time.time_ns())
    command=[str(VERILATOR),'--cc','--compiler','gcc','--top-module',top,'--prefix',prefix,'--Mdir',object_dir,'-Wno-fatal','--no-timing']+verilog
    r=subprocess.run(command,cwd=folder,env=env,capture_output=True,text=True,errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
    (folder/'verilate.log').write_text(r.stdout+r.stderr,'utf-8');assert r.returncode==0,r.stderr
    generated=[str(p.relative_to(folder)) for p in (folder/object_dir).glob('*.cpp')]
    command=[str(GXX),'-O2','-std=c++17','-static','-static-libgcc','-static-libstdc++','-pthread','-I'+object_dir,'-I'+str(INCLUDE),'-I'+str(INCLUDE/'vltstd'),cpp]+generated+[str(INCLUDE/'verilated.cpp'),str(INCLUDE/'verilated_threads.cpp'),'-lpsapi','-o','sim.exe']
    compile_env=dict(os.environ);compile_env['TEMP']=compile_env['TMP']=str(folder)
    start=time.monotonic();r=subprocess.run(command,cwd=folder,env=compile_env,capture_output=True,text=True,errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
    (folder/'cpp_compile.log').write_text(r.stdout+r.stderr,'utf-8')
    print('CPP_BUILD',r.returncode,round(time.monotonic()-start,1),flush=True);assert r.returncode==0,r.stderr[:6000]
    return folder/'sim.exe'
