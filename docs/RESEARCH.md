# 模型与算法选型 / 调研记录

核查日期：2026-09-05。本文区分“研究候选”“可调用适配器”“实测质量”。没有本地 GPU 对照试听/客观基准，因此不授予任何 checkpoint “当前全球最好”的标签。SDR 数字受数据集、reference、算法配置和评估版本影响，不可跨榜直接排序。

## 1. 原唱与伴奏分离

BS-RoFormer / MelBand RoFormer 是本版选定的本地模型家族。推理由 [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) 封装，该项目明确支持这些模型，以及去噪/去混响等任务；固定安装 0.41.1，而不是跟随 main 漂移。家族实现参考 [lucidrains/BS-RoFormer](https://github.com/lucidrains/BS-RoFormer)、[MSST](https://github.com/ZFTurbo/Music-Source-Separation-Training)、[Kimberley 模型](https://github.com/KimberleyJensen/Mel-Band-Roformer-Vocal-Model)。

目前审核进代码的白名单是已知基线，不代表最新模型清单：

| ID | checkpoint | 角色 |
|---|---|---|
| bs-roformer | model_bs_roformer_ep_317_sdr_12.9755.ckpt | ViperX，双轨分离基线 |
| melband | vocals_mel_band_roformer.ckpt | Kim MelBand，人声提取基线 |
| denoise | denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt | 录音去噪候选 |
| dereverb | dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt | 人声去混响候选 |
| ensemble | 前两个模型串行运行 | 两份人声平均后 residual 伴奏，需 A/B |

初始配置 batch=1、使用模型原生 segment、balanced overlap=4 / quality overlap=8。支持 autocast；不未经验证就打开动态编译/native FP16，以免扩展到新 GPU 时增加故障面。所有神经任务仍须在目标机验证。

输出统一 48k；伴奏定义为 mixture − vocals，保证重构一致性。这个定义不会神奇消除 bleed/artifacts；模型各自归一化差异也可能影响 residual 的泄漏，应在真实录音中检查。Ensemble 是可选候选，不保证胜过任一单模型。

### 2026 更新不应被忽略

[MVSEP 官方新闻](https://mvsep.com/en/news) 已公布 HyperACE v2、becruily deux、更多 karaoke/DeReverb 更新；[官方质量评估队列](https://mvsep.com/quality_checker/queue) 也出现了 BS PolarFormer 等候选。这说明只看到“RoFormer”就认定某个旧 checkpoint 仍然是最佳是错误的。

这些更新**没有被本次实现宣称为已集成**：需要进一步核实公开权重、配置、许可证、0.41.1 adapter 是否支持其确切结构，再在 5090 Laptop 上比较音质/显存。部分服务公布的新模型不一定同时给出可再分发本地权重。升级入口在 `backend/app/models.py` 的白名单与 schema/frontend model selector，禁止用户任意上传 pickle checkpoint。

本版离“最前沿、最佳本地分离”仍有验收差距。推荐的升级验收方法是固定 10–20 段已获授权的不同曲风样本，以人声残留、乐器损伤、混响残留、瞬态、立体声形象与资源消耗综合比较；不要只看文件名自带 SDR。

## 2. 变 Key：神经网络不是必要条件

使用 [Rubber Band 官方集成建议](https://breakfastquay.com/rubberband/integration.html) 与 [C ABI](https://breakfastquay.com/rubberband/code-doc/rubberband-c_8h.html)。本版直接 ctypes 调用原生库，选择 R3/EngineFiner、ChannelsTogether、FormantPreserved、offline；先 study 完整结构再 process。

PitchHighQuality 选项也被传入，但官方说明该选择主要控制实时模式，不能把它当成离线额外质量加成。核心有效选择是 R3 与双遍离线流程。原生库本身可独立部署，符合“不在主机建 AI 环境”的方向；整个应用仍然依赖容器内 Python，不是假装使用 llama.cpp/GGUF。

实测纯音 −5/+3/+12 半音的频率与时长；0 半音直接原文件复制。纯音测试不等于对复杂音乐的完整感知质量评估。强移调、人声共振峰与混合乐器仍有取舍。

## 3. 自动调音：检测与重合成是两个问题

真实神经路径是 [TorchCREPE](https://github.com/maxrmorrison/torchcrepe) full 模型估 F0/periodicity；CPU 基线路径为 [librosa pYIN](https://librosa.org/doc/latest/generated/librosa.pyin.html)。20 秒带重叠块处理、置信度/能量筛选，避免把气声和静音都当稳定音符。

调音目标由用户声明的音阶和可编辑音符决定。修正量可平滑、强度可调，尽量保留原 F0 微变化，再通过 [Parselmouth / Praat pitch manipulation](https://parselmouth.readthedocs.io/en/stable/examples/pitch_manipulation.html) 的 PSOLA 重合成。不是训练新的“调音模型”，也不把 TorchCREPE 音高检测称为端到端 AI 调音。当前主要适用于干净的单音干声，不适合混合原曲/复调和声输入。

这类确定性、可视化目标适合第一版，容易审计且不改变歌词。但不等同于 Melodyne 或 Auto-Tune 的完整算法、实时延迟表现或专业表情控制。没有证据可以保证 PSOLA 在所有唱法中胜过商用品。

## 4. 自动和声：本版明确区分 DSP 与生成式 AI

当前实现：从音高轨迹计算音阶第 ±2/±4/+7 级变换，1–3 声部，各自 PSOLA 渲染，轻微左右声像/延迟与无声区抑制。这里“+2 级”在自然音阶里通常形成三度关系，不是固定 +2 半音。

局限：没有从伴奏自动识别随时间变化的和弦、没有对位/声部进行规划、没有生成新的唱法/音色，同一干声重合成仍可能有合唱复制感。复杂和声、转调或大幅移调需要手工选择目标。本项不能冒充“最前沿自动和声模型”。

重点调研了 MIT 的 [AI Harmonizer 论文](https://arxiv.org/abs/2506.18143) 与 [实现](https://github.com/mitmedialab/ai-harmonizer-nime2025)。其流程结合音符转录、Anticipatory Music Transformer、RMVPE/RVC 等技术，做离线四声部生成；它不是一个可直接下载的通用“和声 GGUF”。声学端涉及目标声音模型；公开研究系统的训练域和依赖也需要单独评估。没有未经用户许可代训声音模型、没有下载来历不明歌手模型、没有把现有 DSP 输出标成该研究系统的结果。

后续真正生成式方案的验收门槛：得到可部署模型和清晰许可；和弦约束与声部进行可控；保留用户歌词/节奏；权重下载与显存测试；允许生成式输出失败时显式退回 DSP 而不混淆标签。**本次没有完成这条生成式链路。**

## 5. 降噪、混音与工程体验

给唱歌干声优先可调轻量 DSP，再给出可选人声去噪/去混响模型。语音增强模型的“语音质量高”不自动代表它保留颤音、气息、尾音和歌唱音色；本版没有把 DeepFilterNet/Demucs 等未经唱歌验证的替代品硬套为“最佳”。

后期自动化主要是 preset + 明确参数 + LUFS 初始平衡 + 双遍导出。不是一个神秘“AI 一键母带”。相关一次源：[FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html)、[Pedalboard](https://github.com/spotify/pedalboard)。

## 6. 环境与版权

原生音频部分走 C/C++ 库；神经部分走 CUDA Docker。主机只管理 Docker/驱动，不需要 Conda。没有端到端免依赖单 EXE，也没有已经打好的镜像可以声称开箱即用；GPU 构建和权重需用户端验收。

源代码许可、模型权重许可、训练数据授权和处理商业歌曲的权利是不同事情。models.py 的 SHA256 是本次实际下载文件的溯源记录，不是上游已签名可信哈希。白名单降低风险但不能让 PyTorch pickle 从根本上安全；只信任已审查的上游分发。
