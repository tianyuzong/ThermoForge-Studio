"""Host execution limits; these do not alter simulation physics or output times."""
import os


def memory_bytes():
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ('total', 'available', 'total_page', 'available_page',
                           'total_virtual', 'available_virtual', 'extended')]]
        value = MemoryStatus(); value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return int(value.total), int(value.available)
    else:
        try:
            page = os.sysconf('SC_PAGE_SIZE')
            return page * os.sysconf('SC_PHYS_PAGES'), page * os.sysconf('SC_AVPHYS_PAGES')
        except (ValueError, OSError, AttributeError):
            pass
    return None, None


def execution_policy():
    total, available = memory_bytes(); gib = 1024 ** 3
    automatic = 2 if total is not None and total >= 48 * gib and (os.cpu_count() or 1) >= 8 else 1
    try:
        limit = max(1, min(2, int(os.environ.get('THERMAL_MAX_PARALLEL_JOBS', automatic))))
    except ValueError:
        limit = automatic
    return dict(max_parallel_jobs=limit, total_memory_bytes=total,
                available_memory_bytes=available, additional_job_memory_floor_bytes=16 * gib)


def can_admit_job(active, policy=None):
    policy = policy or execution_policy()
    if active >= policy['max_parallel_jobs']:
        return False
    available = policy['available_memory_bytes']
    return active == 0 or (available is not None and available >= policy['additional_job_memory_floor_bytes'])


def worker_environment():
    env=os.environ.copy()
    # Separate solve processes supply coarse parallelism. Avoid nested BLAS
    # pools competing for every CPU core inside each worker.
    try:threads=max(1,min(os.cpu_count() or 1,int(env.get('THERMAL_CPU_THREADS','1'))))
    except ValueError:threads=1
    for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS'):
        env[key]=str(threads)
    return env
