"""Retired code-only installer; leaves the target unchanged."""
import argparse

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Existing installation (left unchanged)")
    parser.parse_args()
    parser.exit(2, "此旧版补丁安装器已停用，未修改任何文件。\n"
                "v1.2.2 及以上请在原 WebUI 点击‘检查更新’，由更新器迁移统一 Python 环境。\n"
                "更早版本请将新版完整版解压到新目录，设置已有模型路径后启动；保留原目录和作品。\n"
                "请勿只覆盖代码，否则旧运行环境无法启动新版。\n")

if __name__ == "__main__":
    main()
