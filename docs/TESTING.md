# 测试记录与未验证范围

本报告只描述实际执行的测试，不把编译、mock 或 CPU 检查包装成目标 GPU/音频设备验收。

## 已完成

| 层级 | 结果 | 范围 |
|---|---|---|
| 后端 pytest | **46 passed，65.32 秒** | API、真实音频、revision、隔离任务与错误路径 |
| 前端 strict TypeScript | **通过** | strict / noUncheckedIndexedAccess / noUnusedLocals |
| Node 原生测试 | **13 passed** | Worklet PCM 帧边界、无输出泄漏、分块、停止/重新录制、float WAV、自动化与 UI helpers |
| Playwright 界面检查 | **7 项通过，无未捕获 JS 错误** | 实际前端 + 真实本地 API 桥接、3 条真实波形、1536px/390px 布局、混音写入与撤销 |
| 本机 doctor | **通过 CPU/native 部分** | 真正 R3 双遍处理、FFmpeg 及音频模块导入 |

原始记录：[pytest.xml](test-results/pytest.xml)、[frontend.tap](test-results/frontend.tap)、[environment.json](test-results/environment.json)、[visual-report.json](screenshots/visual-report.json)。截图是实际构建出的界面，不是手绘效果图；演示工程为原创合成素材，未使用商业歌曲。

## 后端具体覆盖

- MP3 真实编码再解码；标准 WAV 导入、无效/空/过大上传拒绝；HTTP Range；跨工程资产拒绝。
- 变调 −5、+3、+12 半音的纯音频率与长度，0 半音原样复制；再次变调从根资产渲染；声明调性随 key 变化。
- 抹除区域不改变时长，边界渐变；补录交叉淡化；不完整补录失败但保留原轨/raw take。
- FX 真实输出；静音/独奏、声像、偏移、增益自动化真实混音；WAV/FLAC/MP3 双遍 loudnorm 导出。
- pYIN 真正分析合成单音信号；Praat PSOLA 真正重合成、手动目标/音阶；真实子进程完成音高和和声任务；强度 0 调音文件逐字节相同。
- SQLite 快照、撤销/重做、revision 冲突、工程锁、重启状态；Host/Origin/write-header 校验；不使用假 AI 结果掩盖缺少神经依赖。
- 取消使用真正的独立 sleep 进程验证终止与不提交 revision；**没有用此替代 GPU 推理取消测试**。

测试全部使用程序合成信号，频率和时序正确不等于唱歌听感已达到专业质量。没有 SDR/SAR/SIR 大规模基准、主观盲听或生产歌曲鲁棒性评价。

## 浏览器环境限制

本环境的托管 Chromium 阻止 localhost/file URL 原生导航，出现 `ERR_BLOCKED_BY_ADMINISTRATOR`。没有改写或禁用托管策略。测试改用允许的 `page.set_content` 渲染同一份已构建 JS/CSS，并通过测试专用 Playwright binding 转发 `/api/` 到真实本地服务。

桥接测试不编造 API 数据，项目、波形、混音更新和撤销均由真正后端执行。但它**不验证**原生浏览器网络/CSP/ES module 加载、Web Audio 播放时钟、安全上下文下的麦克风/AudioWorklet 和实际设备 I/O。这些在结果中明确标为 not_verified。

`node:test` 的 Worklet 验证是在 AudioWorkletProcessor/消息端口替身上运行真实处理函数，不是连接麦克风。它能证明程序的帧选择/分块逻辑，不证明真实驱动延迟或没有掉帧。

用户的正常本机浏览器可执行完整 HTTP UI 冒烟模式：

```bash
# 服务先启动；这些只用于开发测试，不是日常运行要求。
pip install playwright httpx
playwright install chromium
python scripts/visual_smoke.py --url http://localhost:7860
```

不要把 `--bridge` 放进生产应用；它只在测试脚本中存在。原生模式包含播放时钟前进的断言，本次尚未通过原生模式验收。

## 仍未运行的验收

**Docker CPU/GPU 镜像构建**：当前工具环境无 Docker，不能验证 apt/pip/npm 在 Docker 内完整解析。这里的 CPU 测试环境是 Python 3.13 / NumPy 2.3.5 / SciPy 1.17；拟交付 Docker 是 Python 3.11 / NumPy 2.2.6 / SciPy 1.13.1，为满足上游 separator 的约束。CI 已编写，但因为远端仓库未创建，没有真实 GitHub Actions 运行结果。

**实际 NVIDIA GPU**：当前只有 torch CPU，未加载任何 RoFormer/TorchCREPE 权重，没有 RTX 5090 Laptop 的速度/显存/兼容性/音质测试，也没有验证模型下载 URL 目前可用。adapter 接口与上游源码核对不等于实测推理。

**麦克风与输出设备**：未测 USB/蓝牙/板载输入、系统权限、丢设备、后台节流和长时间录音。补录与导出服务端链路是实测的，但从真实麦克风到成品的完整链路仍待目标机验收。

**发布**：PowerShell 脚本未在 Windows 运行；Bash 语法检查通过。没有实际创建 GitHub 仓库、推送 commit 或发布容器镜像，提供脚本不能代替这些动作的成功证明。


## 0.1.1 Windows BAT 启动器

新增 `scripts/tests/windows-launcher.Tests.ps1`，由 Windows Actions job 在 Windows PowerShell 5.1 下执行。它调用真正的 BAT 入口并使用 mock `docker.cmd`；覆盖 GPU/CPU 模式、参数转发、特殊字符目录、Compose 版本、远端 endpoint 拦截、Windows containers、原生 stderr、构建失败、doctor 失败、停止保留数据及日志保存。结果由 CI 上传为 `windows-launcher-report`。

本次 Linux 编辑环境没有 Windows/cmd.exe、PowerShell 或 Docker。不能将这些新增 Windows 测试写成已在本地通过；以该提交的 Actions 状态为准。也不能以 mock 测试替代目标 GPU、真实麦克风和模型推理验收。

本轮重新执行：后端 46 项通过，前端 13 项通过，TypeScript 构建通过。BAT 的 ASCII/CRLF、入口文件引用、退出码保留和源码白名单已做静态检查，见 `docs/test-results/bat-package-checks.json`；这不是 Windows 执行结果。
