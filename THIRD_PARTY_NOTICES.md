# 素材与第三方组件

项目原创代码采用根目录 `LICENSE` 中的 MIT 许可证。第三方组件保留各自的版权和许可证；本项目的 MIT 声明不替代其原有许可。

Windows 构建包含 Python、Tcl/Tk、Playwright 及其 Node 驱动、Pillow、greenlet、pyee、typing_extensions 和 PyInstaller 运行部分。构建脚本将已安装发行包中的许可文件复制到 `third-party-licenses/`，并保留 Playwright 驱动目录自带的 LICENSE、NOTICE 和 ThirdPartyNotices。具体版本以依赖清单和所分发包内声明为准。

运行时界面背景与图标是开发阶段通过 imagegen 生成的本地 PNG，不是雨课堂平台提供的官方视觉资源。素材来源和生成提示词见 `yuketang_helper/assets/ui-v2/SOURCE.md`、`docs/assets/`；软件运行时只读取本地图片，不调用图片生成服务。设计参考图用于说明视觉决策，不代表平台官方界面或平台授权。

本项目是个人开发的非官方工具，与雨课堂平台运营方无隶属关系。平台网页、课程内容和服务商模型不随本项目提供；用户自行配置其可访问的服务。
