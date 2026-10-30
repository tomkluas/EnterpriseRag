"""
一键打包脚本：python scripts/build_exe.py
执行完成后，build/企业RAG系统_v1.0/ 即为完整交付文件夹

依赖：开发机需要先安装好 Node.js（构建前端）和本项目 requirements.txt（含 pyinstaller）。
这是开发者/打包者使用的脚本，最终用户不会执行此脚本——他们只会拿到打包好的 exe。
"""
import argparse
import subprocess
import shutil
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
OUTPUT = ROOT / "build" / "企业RAG系统_v1.0"


def find_npm() -> str:
    """查找 npm 命令路径"""
    # 优先使用 npm.cmd（Windows 标准）
    if sys.platform == "win32":
        for name in ["npm.cmd", "npm"]:
            try:
                result = subprocess.run([name, "--version"], capture_output=True, text=True)
                if result.returncode == 0:
                    return name
            except FileNotFoundError:
                continue
        
        # 尝试从常见安装路径查找
        common_paths = [
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "npm.cmd"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "nodejs", "npm.cmd"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "nvs", "node", "18", "x64", "npm.cmd"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "nvs", "node", "20", "x64", "npm.cmd"),
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        
    return "npm"


def step(msg):
    print(f'\n{"=" * 50}\n{msg}\n{"=" * 50}')


def main():
    parser = argparse.ArgumentParser(description="构建企业 RAG 系统交付包")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT,
                        help="交付目录，默认 build/企业RAG系统_v1.0")
    output = parser.parse_args().output_dir.resolve()
    npm_cmd = find_npm()
    print(f"检测到 npm 命令: {npm_cmd}")
    
    step("1/5 构建前端")
    subprocess.run([npm_cmd, "install"], cwd=FRONTEND, check=True)
    subprocess.run([npm_cmd, "run", "build"], cwd=FRONTEND, check=True)

    step("2/5 复制前端产物到后端目录")
    dist_target = BACKEND / "frontend_dist"
    if dist_target.exists():
        shutil.rmtree(dist_target)
    shutil.copytree(FRONTEND / "dist", dist_target)

    step("3/5 PyInstaller 打包")
    icon_path = BACKEND / "resources" / "icon.ico"
    spec_path = BACKEND / "rag_system.spec"
    if not icon_path.exists():
        print(f"⚠ 未找到图标文件 {icon_path}，将使用 PyInstaller 默认图标打包")
    subprocess.run([sys.executable, "-m", "PyInstaller", str(spec_path), "--clean", "--noconfirm"], cwd=BACKEND, check=True)

    step("4/5 整理交付文件夹")
    output.mkdir(parents=True, exist_ok=True)

    exe_name = "启动RAG系统.exe" if sys.platform == "win32" else "启动RAG系统"
    built_exe = BACKEND / "dist" / exe_name
    if not built_exe.exists():
        # macOS/Linux 上 PyInstaller 产物没有 .exe 后缀，兜底查找
        candidates = list((BACKEND / "dist").glob("启动RAG系统*"))
        if candidates:
            built_exe = candidates[0]
    # 构建成功不代表程序能启动；必须运行真实产物后再替换交付目录里的 exe。
    check = subprocess.run([str(built_exe), "--check-startup"], cwd=BACKEND,
                           capture_output=True, timeout=90)
    if check.returncode != 0:
        detail = (check.stdout + check.stderr).decode("utf-8", errors="replace")
        raise RuntimeError(f"打包产物启动自检失败，退出码 0x{check.returncode & 0xffffffff:08X}\n{detail}")
    print(check.stdout.decode("utf-8", errors="replace"))
    shutil.copy(built_exe, output)

    doc1 = ROOT / "配置说明.txt"
    doc2 = ROOT / "使用手册.pdf"
    if doc1.exists():
        shutil.copy(doc1, output)
    else:
        print(f"⚠ 未找到 {doc1}，交付包将缺少该文档")
    if doc2.exists():
        shutil.copy(doc2, output)
    else:
        print(f"⚠ 未找到 {doc2}，交付包将缺少该文档")

    step("5/5 完成！")
    print(f"交付文件夹: {output}")
    print("测试方法：进入该文件夹，双击 启动RAG系统.exe")


if __name__ == "__main__":
    main()
