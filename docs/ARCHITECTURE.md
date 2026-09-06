# 架构与音频不变量

## 分层

```text
Browser / localhost
  Canvas pixel tide + waveform + piano roll
  Web Audio shared clock -> track pan/gain -> monitor gain -> safety compressor
  getUserMedia -> AudioWorklet PCM capture -> WAV upload
        |
        v
FastAPI / single ASGI worker
  Host + Origin + custom-header write checks
  Pydantic allowlisted requests / project-scoped assets / Range responses
        |
        +--> SQLite WAL: projects, immutable assets, revisions, job states
        |
        +--> One global queue -> one isolated job process
                  FFmpeg / Rubber Band C ABI / Praat / RoFormer / TorchCREPE
                  progress.json -> result.json -> atomic project revision
```

浏览器不运行 RoFormer，不把音频送给第三方 API。模型与重型依赖惰性导入，只存在于需要它们的任务子进程。没有 Redis/Celery、外部数据库或云存储的部署负担。由单个 API 实例控制并发，不支持 `uvicorn --workers 2`。

## 音频资产

规范内部格式 48,000 Hz、双声道、float32 WAV。所有实际音频资产不可变，以随机 ID 标识；服务端路径从工程 ID/资产登记生成，客户端不能提交文件路径或 shell 命令。资产登记包含 duration、frames、peak/RMS、波形峰值与 SHA256。

工程轨道引用 asset_id、source_asset_id、offset、gain_db、pan、muted、solo 与增益自动化。波形是实际文件计算的缩略数据；演示素材是程序合成，不是 AI 推理输出。谱图编辑尚未实现。

变 Key 只处理 original/reference/accompaniment。source_asset_id 始终指向该轨的根版本，避免 0→3→5 的累计压缩和变调。用户录音不会随伴奏自动转调，以免已经唱对的 take 被再移一次。改变 Key 后应重新录唱或明确对目标音轨做调音。

## 非破坏性修改与并发

普通编辑使用 revision 乐观锁；过期写入 409。每次工程变更保存完整轨道快照，撤销/重做只切换引用，音频文件不被就地改写。自动处理成功后才在事务内应用结果；处理时锁住对应工程。一个模型/DSP任务失败不会提交半个 mix。

录音先保存原始 take，再启动补录合成。完整补录才替换目标区间；短 take 保留但任务失败。前端也会在提前停止时主动另存独立 take。补录失败/取消并不删除已保存的 raw take。

数据目录目前没有自动垃圾回收，历史会占用磁盘。SQLite 事务不等于跨所有音频文件的断电事务；意外退出可能留下未引用的临时输出，需要未来的显式恢复/清理工具，不能声称完整 crash consistency。

## 任务生命周期

`queued -> running -> completed / failed / cancelled`。重启时，把未完成任务明确标记 failed，让用户自行重试，不偷偷恢复可能存在副作用的渲染。队列最大 8，单任务默认 7200 秒。结果/进度 JSON 原子替换；日志由子进程输出。

取消会终止工作进程组，连带其 FFmpeg 子进程。取消测试用真实 sleep 子进程验证隔离机制，不是 GPU kernel cancellation 实测。最终工程 revision 只在结果通过路径/格式校验后更新。

## 播放、录音和延迟

所有轨道通过同一个 AudioContext 时钟调度。片段起点、局部自动化与播放位置使用秒/帧对应关系；gain 自动化按 dB 线性插值，在 Web Audio 中用指数 amplitude ramp 表达，导出端做相同的 dB 插值。StereoPanner 的双声道计算与离线混音一致。

录音只把麦克风送入 PCM worklet；伴奏播放不接入录音节点，工作节点的输出始终为零。软件耳返是独立增益通路且默认关闭，避免啸叫。请求关闭 AEC/AGC/浏览器降噪，但硬件/浏览器可以不满足请求，因此显示实际 constraints。

预备拍与录制边界使用 sample frame，避免依赖 setTimeout 精度。硬件输入/输出、USB/蓝牙仍然有未知延迟；当前只提供手动校准，不承诺“自动零延迟”。JS 主线程保存分块 PCM，最长录音会占内存；浏览器关闭前尚未传到服务端的内容不保证可恢复。

## 后期与母带

FFT 宽带降噪、滤波/EQ、压缩、齿音由 FFmpeg；混响和节拍关联延迟由 Pedalboard。空间效果预留两秒尾音，极长混响尾巴可能被截断。音高使用 pYIN 或实际 TorchCREPE，重合成用 Praat PSOLA，限定单音人声。

和声是音阶级移位加原 F0 细节，不是生成的独立演唱者。默认和声减小音量、左右展开、轻微时间偏移并压低无声区，听感仍需审听。自动混音是响度初始平衡启发式，不是 AI 母带。

导出混音按轨道 mute/solo/pan/gain/automation/offset 渲染，然后双遍 loudnorm。试听总音量和浏览器保护压缩器不写入导出；所以试听和归一化母带不承诺 sample-identical，但轨道混音配置一致。极端高动态素材、MP3 编码后峰值均需复测，不保证响度目标总能在不改变动态的条件下同时满足。

## 主要 API

`GET /api/health`、`GET /api/models`；`GET /api/projects`、`POST /api/projects/import`、`POST /api/projects/demo`；工程 GET/PATCH；轨道 PATCH/DELETE；录音 POST；undo/redo POST；assets GET 支持 Range；jobs POST/GET/cancel/log/download；manifest GET。

交互文档在 `/api/docs`；写入必须带 `X-KTV-Client: 1`。API 文档页的 CDN 资源需要联网，不影响主应用完全本地运行。
