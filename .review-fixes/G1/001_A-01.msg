fix(utils): stop closing atomic-write fd twice on replace failure

write_bytes_atomic and write_secret_atomic closed the temp fd again in the
except branch after os.fdopen's with-block had already closed it. When
os.replace failed (e.g. PermissionError on Windows while a reader holds the
target), the fd number could already belong to another thread's file/socket.
Track whether fdopen owns the fd and only close it ourselves before handover.

Refs: A-01
