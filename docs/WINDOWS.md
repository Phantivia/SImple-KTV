# Windows：双击 BAT 启动

## 最简单的用法

使用本次交付的完整 ZIP（远端源码尚未同步），**先全部解压**到可写的本地目录。不要只下载 BAT，不要在压缩包预览窗口内运行，也不要双击 `index.html`。

| 文件 | 用途 |
|---|---|
| `start.bat` | 默认 NVIDIA GPU 模式：构建/启动 → 健康检查 → 原生音频和 CUDA 诊断 → 打开浏览器 |
| `start-cpu.bat` | CPU / DSP 体验模式，不安装 RoFormer 和 TorchCREPE 神经模型依赖 |
| `stop.bat` | 停止当前服务，保留工程、录音、历史与模型缓存 |
| `logs.bat` | 持续查看服务日志；关闭此窗口不会停止应用 |
| `diagnose.bat` | 对已经启动的容器重新执行音频库和 GPU（如适用）诊断 |

**你的 RTX 5090 Laptop 使用 `start.bat`。** 启动成功后浏览器地址为 <http://localhost:7860>。关闭启动窗口不等于停止容器；需要停止时双击 `stop.bat`。

## 一次性准备

需要 Windows 自带的 PowerShell 5.1、Docker Desktop、WSL2 后端和支持实际 GPU 的 NVIDIA 驱动。无需在主机上安装 Python、Conda、Node、PyTorch 或 CUDA Toolkit。

- [Docker Desktop Windows 安装说明](https://docs.docker.com/desktop/setup/install/windows-install/)
- [Windows GPU 容器要求：WSL2](https://docs.docker.com/desktop/features/gpu/)
- [Compose `gpus` 要求 2.30.0 或更新版本](https://docs.docker.com/reference/compose-file/services/#gpus)

Docker Desktop 第一次安装、授权、WSL 更新和可能的系统重启需要你完成；BAT 不会偷偷安装驱动、提升权限或更改系统设置。发现 Docker Desktop 已安装但未启动时，启动器会尝试打开它并等待 Engine。

**这是 BAT + Docker 版，不是把模型和运行库全部塞进 ZIP 的免安装离线版。** 第一次构建需要联网下载容器及依赖；第一次模型处理需要下载对应权重。后续启动复用构建缓存，模型保存在独立持久化 volume。请给 Docker 数据盘留出足够空间。

## 失败时不会一闪而过

BAT 默认在结束时暂停，显示错误与退出状态；每次运行的记录保存在 `logs/`。构建输出实时显示，启动失败时还会尝试附加最后 100 行容器日志。

| 提示 | 处理 |
|---|---|
| 找不到 Docker Desktop | 安装并完成初次设置，再运行 BAT |
| Docker Engine 未就绪 | 打开 Docker Desktop，确认 WSL2 后端、系统虚拟化与 WSL 状态 |
| Compose 版本太旧 | 更新 Docker Desktop，要求 Compose ≥ 2.30.0 |
| Windows-container mode | 切换到 Linux containers；GPU 模式使用 WSL2 |
| Remote/TCP Docker endpoint | 选回本机 Docker Desktop Linux context；启动器不会把代码发送到远端 daemon |
| 7860 端口已占用 | 关闭占用该端口的其他服务，再启动；不要通过删除数据解决端口问题 |
| 依赖下载/镜像构建失败 | 查看 `logs/start-*.log`；检查网络和 Docker 磁盘，修复后重试 |
| CUDA/doctor 失败 | 查看诊断日志，检查驱动与 GPU passthrough；不会默默降级为 CPU |
| 麦克风无权限 | 通过 localhost 访问，允许浏览器麦克风权限，检查 Windows 隐私设置 |
| 企业策略阻止 PowerShell | 联系设备管理员；进程级 `ExecutionPolicy Bypass` 不会绕过组织强制策略 |

GPU 诊断失败时容器可能仍在运行；`stop.bat` 可安全停止。仅想体验界面及 DSP 功能时明确使用 `start-cpu.bat`，它不具备原唱 AI 分离能力。

## 可选命令行参数

```bat
start.bat -NoBrowser
start.bat -NoBuild -NoBrowser
start.bat -CPU
start.bat -WaitSeconds 600
logs.bat -NoFollow
```

`-NoBuild` 只适合本机已经有相应镜像且源码未变化时使用；默认启动会检查并利用 Docker 构建缓存更新镜像。`-WaitSeconds` 控制 Docker Desktop/服务健康等待，不是模型推理超时。

自动化执行时可设置 `KTV_NO_PAUSE=1`，BAT 将保留正确退出码、不等待按键。正常双击无需任何参数。

## 数据与安全

`simple-ktv_ktv-data` 保存原歌曲、录音、数据库、历史及导出；`simple-ktv_ktv-models` 保存模型。GPU/CPU 启动使用同一个固定 Compose project，不会因解压目录改名而自动新建另一套数据卷。

所有 BAT 都不会执行 `down -v`、卷清理、数据覆盖或自动 Git 更新。应用端口只绑定本机，不是可直接公开部署的多人服务。`logs/` 已排除出 Git；日志可能包含本地路径及错误细节，分享前请检查。

## 验证范围

新增 Windows CI 使用真正的 `.bat → Windows PowerShell 5.1` 调用链和 **mock Docker**，验证参数、特殊字符目录、错误返回、日志、GPU/CPU 分流与数据保护。它不等价于真正 Docker 镜像、RTX 5090、声卡和模型权重验收。以仓库 Actions 的实际结果为准；不能把未执行的测试写成通过。


## 将完整版本上传到指定仓库

本次连接器已初始化 `Phantivia/SImple-KTV`，但随后的源码写入被连接器安全检查拦截，不能声称远端已有完整应用。`upload.bat` 是可在你自己电脑上运行的上传入口，**不影响 `start.bat` 的正常使用**。

先安装 [Git](https://git-scm.com/downloads/win) 与 [GitHub CLI](https://cli.github.com/)，在本机执行一次 `gh auth login --hostname github.com`，然后双击 `upload.bat`。它会读取现有仓库、检查公开状态及写入权限、克隆默认分支、仅拷贝 `scripts/source-files.txt` 的源码白名单，显示变更摘要，并要求输入 `YES` 后提交和推送。

不清空远端目录，不修改 Git 全局身份或凭据设置，不 force push，不发布录音、模型、数据库或日志。若远端有并发提交或分支保护，正常推送会失败而不是覆盖它们；临时 checkout 会保留用于检查。此脚本未在本次 Linux 环境实跑 Windows/GitHub 上传。
