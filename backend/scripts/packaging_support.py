"""Keep VC runtime DLLs from a single trusted runtime installation."""
from pathlib import Path
import re


def pin_windows_runtime(entries, runtime_dir: Path):
    runtime_dir = Path(runtime_dir)
    for name in ('msvcp140.dll', 'vcruntime140.dll'):
        if not (runtime_dir / name).is_file():
            raise RuntimeError('构建机器缺少 Microsoft Visual C++ x64 运行库，请安装后重新打包')
    release_name = re.compile(r'^(?:msvcp140(?:_[12]|_atomic_wait|_codecvt_ids)?|vcruntime140(?:_1|_threads)?|concrt140)\.dll$', re.IGNORECASE)
    files = {path.name.lower(): path for pattern in ('msvcp140*.dll', 'vcruntime140*.dll', 'concrt140.dll') for path in runtime_dir.glob(pattern) if release_name.fullmatch(path.name)}
    clean = [entry for entry in entries if Path(entry[0]).name.lower() not in files]
    return clean + [(path.name, str(path), 'BINARY') for path in files.values()]
