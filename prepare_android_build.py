#!/usr/bin/env python3
"""
准备Android构建包
将项目打包成适合在Android设备上构建的格式
"""

import os
import sys
import shutil
import zipfile
from pathlib import Path

def create_android_build_package():
    """创建Android构建包"""
    
    project_dir = Path(__file__).parent
    apk_dir = project_dir / "apk_portable"
    output_dir = project_dir / "android_build_package"
    
    print("=" * 60)
    print("MH-DeepSeek Android Build Package Creator")
    print("=" * 60)
    
    # 清理输出目录
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. 复制APK构建文件
    print("\n[1/4] 复制APK构建文件...")
    shutil.copytree(apk_dir, output_dir / "apk_portable", dirs_exist_ok=True)
    
    # 2. 创建Python代码包（如果不存在）
    python_bundle = apk_dir / "assets" / "python_bundle.zip"
    if not python_bundle.exists():
        print("\n[2/4] 创建Python代码包...")
        create_python_bundle(project_dir, python_bundle)
    else:
        print("\n[2/4] 使用现有的Python代码包...")
    
    # 3. 复制构建脚本
    print("\n[3/4] 复制构建脚本...")
    shutil.copy2(project_dir / "BUILD_GUIDE.md", output_dir / "BUILD_GUIDE.md")
    
    # 4. 创建传输包
    print("\n[4/4] 创建ZIP传输包...")
    zip_path = project_dir / "mh-deepseek-android-build.zip"
    create_zip_package(output_dir, zip_path)
    
    print("\n" + "=" * 60)
    print("构建包准备完成！")
    print(f"输出文件: {zip_path}")
    print(f"大小: {zip_path.stat().st_size / 1024 / 1024:.2f} MB")
    print("\n下一步:")
    print("1. 将ZIP文件传输到Android设备")
    print("2. 解压文件")
    print("3. 按照BUILD_GUIDE.md中的说明构建APK")
    print("=" * 60)

def create_python_bundle(project_dir, output_path):
    """创建Python代码包"""
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 要包含的文件扩展名
    include_extensions = {'.py', '.txt', '.toml', '.md', '.html', '.bat', '.ps1', '.sh'}
    
    # 要跳过的目录
    skip_dirs = {'__pycache__', '.git', 'apk_portable', 'node_modules', 'venv', '.idea',
                 'agent_memories', 'agent_storage', 'esp32_repeater', 'plugins', 'tools',
                 'android_build_package', 'build_work'}
    
    files_added = []
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_dir):
            # 过滤目录
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
            
            for file in files:
                # 检查文件扩展名
                if any(file.endswith(ext) for ext in include_extensions):
                    file_path = Path(root) / file
                    try:
                        arcname = file_path.relative_to(project_dir)
                        # 跳过输出目录
                        if str(arcname).startswith('android_build_package') or str(arcname).startswith('build_work'):
                            continue
                            
                        zf.write(file_path, arcname)
                        files_added.append(str(arcname))
                    except Exception as e:
                        print(f"  跳过 {file_path}: {e}")
    
    print(f"  添加了 {len(files_added)} 个文件到Python包")
    if files_added:
        print("  前10个文件:")
        for f in files_added[:10]:
            print(f"    - {f}")

def create_zip_package(source_dir, output_path):
    """创建ZIP包"""
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(source_dir)
                zf.write(file_path, arcname)
    
    print(f"  创建了ZIP包: {output_path}")

def main():
    """主函数"""
    try:
        create_android_build_package()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())