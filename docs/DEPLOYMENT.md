# 部署、诊断与验收

## 面向 RTX 5090 Laptop

优先 Windows + Docker Desktop 的 WSL2 后端。官方资料明确 Windows GPU 容器使用 WSL2：[Docker GPU support](https://docs.docker.com/desktop/features/gpu/)。主机安装适配实际显卡的 NVIDIA 驱动；镜像安装官方 PyTorch CUDA 12.8 wheels。应用不把 Laptop GPU 的显存与台式机型号混为一谈，实际设备名、显存与 compute capability 由 PyTorch 查询。

镜像固定 torch / torchaudio 2.10.0、torchvision 0.25.0，来自 [PyTorch 官方历史版本安装表](https://pytorch.org/get-started/previous-versions/)。这是一组已公开的 CUDA 12.8 构建，不是声称“今天最新版”。不要使用早期仅 CUDA 11.8/12.1 的环境来推断 Blackwell 已兼容。

启动后执行的 doctor 会做原生 Rubber Band R3 渲染、模块导入，以及 GPU 模式的 CUDA 矩阵乘法。**成功通过矩阵乘法也不等于特定 RoFormer checkpoint 已通过质量/显存验证**，还要实际处理短样本。

## 容器结构

`compose.yaml` 为 CPU：录音、剪辑、Rubber Band、pYIN、PSOLA 和声、DSP 后期、导出可用；不安装重量级神经模型依赖。

`compose.yaml + compose.gpu.yaml` 为 GPU：在 CPU 层上安装 CUDA wheels、audio-separator、TorchCREPE。模型 adapter 使用 PyTorch GPU；`audio-separator[cpu]` 只提供其启动所需的 ONNX Runtime CPU import，并不强制 RoFormer 用 CPU。没有引入另一组 ONNX Runtime GPU/cuDNN 版本组合。

所有服务以非 root UID 1000 运行，端口只绑定 127.0.0.1。镜像构建过程会安装编译器来构建上游旧扩展，**主机不需要编译器**。当前不是已经上传到 GHCR 的预编译镜像，第一次必须在用户端 build；GPU 层下载与磁盘占用可能很大，应提前留足空间。

## 依赖可复现性：当前边界

直接应用依赖有精确版本，CUDA wheels 有 `+cu128` 约束，构建时执行 `pip check` 并记录 `/app/build-info/requirements-resolved.txt`。但**尚未生成经过实跑验证的全部传递依赖哈希锁**，基础镜像也未固定 digest；不能声称 bit-for-bit reproducible。

特别是 audio-separator 0.41.1 的 pyproject 固定 SciPy `^1.13.0`，因此镜像使用 SciPy 1.13.1 和 NumPy 2.2.6。提供本次本地测试的环境与此不同：Python 3.13、NumPy 2.3.5、SciPy 1.17；这就是为什么 CPU 单元测试通过不等于 Docker resolver 已验收。上游依赖还包含 `onnx-weekly`，应在首次成功构建后保存实际 freeze 并进一步锁定。不要盲目 `pip install -U`。

## 首次启动

Windows 完整解压后双击 `start.bat`；CPU 体验双击 `start-cpu.bat`。停止、日志、诊断分别为 `stop.bat`、`logs.bat`、`diagnose.bat`。详见 [BAT 启动指南](WINDOWS.md)。下面的 PowerShell 命令仍兼容。

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
# 不安装神经模型的 CPU 体验：
powershell -ExecutionPolicy Bypass -File .\start.ps1 -CPU
```

进入 `http://localhost:7860`，先打开合成演示，确认 UI 和导出，再导入你有权处理的一段短歌曲，选择模型。

```bash
# 查看启动/任务环境
docker compose -f compose.yaml -f compose.gpu.yaml logs --tail 150
# 重新执行诊断
docker compose -f compose.yaml -f compose.gpu.yaml exec -T ktv python scripts/doctor.py
# 查看实际解析后的依赖版本
docker compose -f compose.yaml -f compose.gpu.yaml exec -T ktv cat /app/build-info/requirements-resolved.txt
```

后台推理在本机进行，API 健康检查不下载权重。首次分离在任务日志中显示下载；没有上游逐块进度时 UI 使用不定进度动画，不编造百分比。

## 故障排查

| 现象 | 检查 |
|---|---|
| `gpus` 配置不被识别 | 更新 Docker Desktop / Compose 到支持 GPU service 配置的版本 |
| CUDA 不可用或 `no kernel image` | 主机驱动、WSL2 GPU、Docker GPU passthrough、wheel 的 CUDA 构建、doctor 真实 kernel 检查 |
| AI 选项灰色 | 是否以 `-CPU` 启动；是否确实构建 GPU target；`/api/health` 中 separation/torchcrepe 是否 true |
| GPU OOM | 先关其他占显存程序、用单模型 balanced，确认 batch=1；ensemble 为串行，但大权重本身仍有显存需求 |
| 模型下载失败 | 网络、上游 URL/registry、磁盘空间；取消任务后修复网络再重试，不删除整个模型目录 |
| 权重或 stem 不匹配 | adapter 会报错，不返回假伴奏；在 models.py 审核模型名与上游配置后重新构建 |
| 依赖解析失败 | 保留完整构建日志，检查 SciPy/NumPy 约束、onnx-weekly、旧 samplerate/diffq 构建；本次未验证此阶段 |
| 麦克风不可用 | 只能在 localhost / HTTPS；检查浏览器站点授权和系统隐私权限；不要打开 file://index.html |
| 输出设备选择不可用 | 浏览器未提供 AudioContext.setSinkId；改用系统声音设置，不是虚假设备选择 |
| 演唱与伴奏错位 | 使用有线耳机；录一段节拍，测量固定延迟，再调“录音对齐补偿”或片段起点 |
| 上传失败 | 不关闭页面，先下载未保存 WAV，再重试；后端文件受大小/时长限制 |
| 工程打开慢、内存很大 | 当前版本预解码所有轨；减少当前工程轨数，注意移除轨不会立即回收历史资产 |

## 数据与完整备份

数据 volume 包含 SQLite、不可变 WAV、处理日志与导出。模型另一个 volume，可以重新下载，但录音不可以。先停止服务，再备份整个 data volume，避免 WAL/音频不一致。不要只保存工程清单 JSON。

示例（PowerShell，备份文件仅留在本机）：

```powershell
.\stop.ps1
New-Item -ItemType Directory -Force backups | Out-Null
$Destination = (Resolve-Path .\backups).Path
# 默认 Compose project name 在 compose.yaml 固定为 simple-ktv。
docker run --rm -v simple-ktv_ktv-data:/source:ro -v "${Destination}:/backup" alpine:3.21 sh -c 'tar czf /backup/ktv-data.tar.gz -C /source .'
```

该命令需要下载 Alpine 镜像；未在本次环境实测。请检查退出码、文件大小并定期做恢复演练。恢复必须在停机时由明确了解 Docker volume 的操作者执行；本项目不会自动覆盖现有数据。原歌曲、录音与用户自己的声音模型都不要公开提交到 GitHub。

## 最低目标机验收清单

先用非敏感短录音完成：实际设备识别 → 原曲分离 → 原曲≈人声+伴奏检查 → −3/+3 Key → 录制/停止/设备断开 → 补录完整/提前结束 → pYIN/TorchCREPE 对比 → 调音/和声 → FX → 导出 → 重启恢复/撤销。

记录 GPU 驱动、运行库、显存峰值、时长、曲种、模型 SHA256、试听伪影与偏差。通过后再增加长歌曲和并行压力。不要用合成演示的假想 SDR 数字作为分离验收。
