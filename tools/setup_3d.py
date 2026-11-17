#!/usr/bin/env python3
"""跨平台 3D 部署，不覆盖已有 .env，也不下载大模型与 2D 运行库。"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def run(args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    node = shutil.which('node')
    if not npm or not node:
        raise SystemExit('请先安装 Node.js 22.12+（含 npm），再重新运行本脚本。')
    major, minor = map(int, subprocess.check_output([node, '-p', 'process.versions.node'], text=True).split('.')[:2])
    if (major, minor) < (22, 12):
        raise SystemExit('需要 Node.js 22.12+。')
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        venv.create(environment, with_pip=True)
    run([python, '-m', 'pip', 'install', '-r', 'requirements.txt'])
    run([npm, 'ci'])
    run([npm, 'run', 'build'])
    if not (ROOT / '.env').exists():
        shutil.copyfile(ROOT / '.env.example', ROOT / '.env')
    print('\n已准备好。先在 .env 填写 DEEPSEEK_API_KEY，再启动：')
    print('  "%s" bridge.py' % python)
    print('  http://127.0.0.1:8000 ；没有 API Key 也可选择「直接朗读」验证。')


if __name__ == '__main__':
    main()
