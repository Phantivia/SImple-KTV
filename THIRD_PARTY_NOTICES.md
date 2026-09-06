# Third-party notices

Simple KTV application source is distributed under GPL-3.0-or-later. The full GPLv3 text is in LICENSE. This repository does not include model weights, commercial songs, or font files. Demo audio is generated from code at runtime.

Components retain their own licenses. Consult the exact installed version and its upstream license before redistributing binaries or container images; this summary is not a substitute for those texts or legal advice.

| Component | Role | Upstream / license reference |
|---|---|---|
| Rubber Band | Native R3 time/pitch processing | https://github.com/breakfastquay/rubberband ; GPL-2.0-or-later or commercial license |
| Praat / Parselmouth | Pitch manipulation and PSOLA | https://github.com/YannickJadoul/Parselmouth ; GPL-3.0-or-later |
| Pedalboard | Reverb/delay | https://github.com/spotify/pedalboard ; GPL-3.0 |
| FFmpeg | Decode, filters and export | https://ffmpeg.org/legal.html ; LGPL/GPL depending on configuration |
| audio-separator | Local neural inference adapters | https://github.com/nomadkaraoke/python-audio-separator ; MIT code, model licenses separate |
| TorchCREPE | Neural pitch detection | https://github.com/maxrmorrison/torchcrepe ; MIT code |
| PyTorch / TorchAudio / TorchVision | Tensor inference | https://pytorch.org/ ; see each package's license notices |
| librosa | CPU pitch analysis/resampling | https://librosa.org/ ; ISC |
| NumPy / SciPy / SoundFile | Numeric/audio processing | https://numpy.org/ ; https://scipy.org/ ; https://python-soundfile.readthedocs.io/ |
| FastAPI / Pydantic / Uvicorn | Local API | See package metadata and upstream MIT/BSD license notices |
| TypeScript | Build-time compiler only | https://github.com/microsoft/TypeScript ; Apache-2.0 |
| NVIDIA CUDA runtime libraries | GPU image runtime | NVIDIA redistributable terms apply independently |

The application asks the upstream separator to download allowlisted model files at first use. A repository's MIT code license does not grant rights to every checkpoint it references, its training recordings, a person's voice, or a user's uploaded song. No commercial-use guarantee or checkpoint relicensing is made.

Container images built from this source include additional transitive packages and Debian components. Preserve notices and meet corresponding-source obligations when redistributing GPL-linked binaries. No font binaries are shipped; the UI uses system fonts and CSS/Canvas graphics.
