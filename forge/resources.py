"""Explicit admission estimates; never reduce a mesh or a load to fit resources."""
import ctypes,os,shutil,math


def available_memory():
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),*[(k,ctypes.c_ulonglong) for k in ('physical','available','page','page_available','virtual','virtual_available','extended')]]
        m=Memory();m.length=ctypes.sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):raise OSError('无法读取可用内存')
        return m.available
    values={line.split(':')[0]:int(line.split()[1])*1024 for line in open('/proc/meminfo') if ':' in line}
    return values['MemAvailable']


def ensure_disk_headroom(folder,minimum_bytes=256*1024**2):
    free=shutil.disk_usage(folder).free
    if free<minimum_bytes:raise ValueError(f'可用磁盘仅 {free/1024**2:.0f} MiB，低于保留阈值；停止后续阶段并保留已提交结果')
    return free


def storage_estimate(mesh_info,case):
    elements=mesh_info['elements'];nodes=mesh_info['nodes'];dofs=mesh_info['dofs']
    thermal=case.analysis_type in ('thermal_steady','thermal_transient','thermo_mechanical')
    transient=case.analysis_type=='thermal_transient' or case.analysis_type=='thermo_mechanical' and case.coupling.transient
    physical_steps=math.ceil(case.thermal.duration_s/min(case.thermal.dt_s,case.thermal.save_s))+1 if transient else 1
    frames=math.ceil(case.thermal.duration_s/case.thermal.save_s)+1 if transient else 1
    if case.history:
        frames=len(case.history.times_s)*(1+case.history.stabilization_cycles)
        if case.analysis_type=='contact_fatigue':frames*=max(1,case.wear_blocks)
        physical_steps=frames
    raw_per_frame=(elements*4*18+nodes*7)*8
    # Text solver output + restart input/output coexist for each mechanical trial.
    per_trial=nodes*2048+elements*8192+dofs*256
    iterations=case.coupling.max_iterations if case.analysis_type=='thermo_mechanical' else 1
    retries=1+case.coupling.max_cutbacks if transient and case.analysis_type=='thermo_mechanical' else 1
    trials=physical_steps*iterations*retries if case.analysis_type not in ('thermal_steady','thermal_transient') else 0
    archive=raw_per_frame*frames*3
    journal=raw_per_frame*physical_steps if thermal else 0
    scratch=per_trial*trials
    return {'archive_bytes':archive,'checkpoint_bytes':journal,'solver_scratch_bytes':scratch,'estimated_total_bytes':archive+journal+scratch,
        'physical_steps':physical_steps,'saved_frames':frames,'trial_budget':trials,'cutback_allowance':retries,
        'adaptive_step_growth_guard':'runtime free-space check; repeated smaller physical steps may exceed this estimate'}


def admit(mesh_info,case,folder):
    if case.resources.cpu_threads>(os.cpu_count() or 1):raise ValueError('请求的 CPU 线程数超过本机逻辑核心数')
    dofs=mesh_info['dofs'];elements=mesh_info['elements'];nodes=mesh_info['nodes']
    if dofs>case.resources.max_dofs:raise ValueError(f'网格需要 {dofs} 自由度，超过工况资源上限 {case.resources.max_dofs}；未自动粗化')
    available=available_memory();budget=min(available*.8,(case.resources.memory_limit_GiB or float('inf'))*1024**3)
    # Conservative 3-D sparse factorization envelope, plus assembly and raw fields.
    estimated=256*dofs**(4/3)+elements*8192+nodes*4096
    if estimated>budget:raise ValueError(f'预计求解内存 {estimated/1024**3:.2f} GiB 超过可用预算 {budget/1024**3:.2f} GiB；请调整资源配置')
    storage=storage_estimate(mesh_info,case);free=ensure_disk_headroom(folder)
    if storage['estimated_total_bytes']>free*.8:raise ValueError(f'包括耦合迭代、减步、重启动与原始场预计需要 {storage["estimated_total_bytes"]/1024**3:.2f} GiB，超过可用磁盘预算；未自动粗化或减载')
    return {'cpu_threads':case.resources.cpu_threads,'estimated_memory_GiB':estimated/1024**3,'memory_budget_GiB':budget/1024**3,'estimated_storage_GiB':storage['estimated_total_bytes']/1024**3,'storage':storage,'method':'conservative trial/restart envelope and sparse-factorization estimate; runtime disk guard, not a measured peak'}
