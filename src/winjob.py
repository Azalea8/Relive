"""Windows Job Object — parent exit → child auto-terminated by kernel."""
import os

if os.name == "nt":
    import win32api
    import win32con
    import win32job


    class JobObject:
        def __init__(self):
            self._hjob = win32job.CreateJobObject(None, "")

            info = win32job.QueryInformationJobObject(
                self._hjob,
                win32job.JobObjectExtendedLimitInformation,
            )
            info["BasicLimitInformation"]["LimitFlags"] = (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self._hjob,
                win32job.JobObjectExtendedLimitInformation,
                info,
            )

        def add_pid(self, pid: int):
            hproc = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                False,
                pid,
            )
            win32job.AssignProcessToJobObject(self._hjob, hproc)
            win32api.CloseHandle(hproc)

else:
    class JobObject:
        pass  # no-op on non-Windows

        def add_pid(self, pid: int):
            pass
