import numpy as np
import pytest
import soundfile as sf
from app import audio
from app.schemas import Effects
from app.store import new_track
from conftest import sine


def frequency(data):
    mono=data.mean(axis=1)
    mono=mono[int(.25*audio.SR):-int(.25*audio.SR)]
    spectrum=abs(np.fft.rfft(mono*np.hanning(len(mono))))
    return np.fft.rfftfreq(len(mono),1/audio.SR)[np.argmax(spectrum)]


def test_metadata_tracks_real_peaks_antiphase(source,tmp_path):
    data=audio.load(source);data[:,1]*=-1
    path=tmp_path/"anti.wav";audio.write(path,data)
    m=audio.metadata(path)
    assert m["frames"]==96000 and m["channels"]==2
    assert -17 < m["peak_db"] < -16
    assert len(m["peaks"])<=1600 and len(m["sha256"])==64
    assert max(p[1] for p in m["peaks"])>.14


@pytest.mark.parametrize("shift",[-5,3,12])
def test_rubberband_changes_pitch_not_duration(source,tmp_path,shift):
    out=tmp_path/"shift.wav";audio.transpose(source,out,shift)
    x=audio.load(out)
    assert len(x)==sf.info(source).frames
    assert abs(frequency(x)-220*2**(shift/12))<3
    assert np.isfinite(x).all()


def test_erase_never_ripples_or_changes_outside():
    x=sine(2); y=audio.erase(x,.5,1.1)
    assert y.shape==x.shape
    np.testing.assert_array_equal(y[:24000],x[:24000])
    np.testing.assert_array_equal(y[52800:],x[52800:])
    assert not y[25000:51000].any()
    with pytest.raises(ValueError):audio.erase(x,3,4)


def test_punch_crossfade_preserves_original_and_rejects_short():
    x=sine(2);take=sine(.6,330)
    y=audio.punch(x,take,.5,1.1)
    np.testing.assert_array_equal(y[:24000],x[:24000])
    np.testing.assert_array_equal(y[52800:],x[52800:])
    np.testing.assert_allclose(y[25000:51500],take[1000:27500])
    assert y[24000,0]==x[24000,0]
    with pytest.raises(ValueError):audio.punch(x,sine(.1),.5,1.1)


def test_pan_and_db_automation_match_stereo_semantics():
    x=np.array([[1.,2.]],dtype=np.float32)
    np.testing.assert_allclose(audio.stereo_pan(x,-1),[[3.,0.]],atol=1e-6)
    np.testing.assert_allclose(audio.stereo_pan(x,1),[[0.,3.]],atol=1e-6)
    np.testing.assert_array_equal(audio.stereo_pan(x,0),x)
    t={"gain_db":-6,"automation":[{"time":0,"db":0},{"time":2,"db":-20}]}
    np.testing.assert_allclose(audio.automation_gain(t,np.array([0.,1.,2.,3.])),10**(np.array([-6.,-16.,-26.,-26.])/20),rtol=1e-6)


def test_mixer_offsets_solos_and_gain(source,tmp_path):
    asset=audio.metadata(source,"one");t=new_track(asset,"track","vocal",offset=.25,gain_db=-6,pan=1)
    silent={**new_track(asset,"muted","vocal"),"muted":True,"gain_db":12}
    p={"duration":2.25,"tracks":[t,silent]};out=tmp_path/"mix.wav"
    audio.mix(p,{"one":str(source)},out);y=audio.load(out)
    assert len(y)==108000 and not y[:12000].any()
    np.testing.assert_allclose(y[12000:,0],0,atol=1e-7)
    np.testing.assert_allclose(y[12000:,1],audio.load(source)[:,0]*2*10**(-6/20),rtol=1e-5,atol=1e-7)
    silent["solo"]=True
    with pytest.raises(ValueError):audio.mix(p,{"one":str(source)},out)


def test_effects_render_finite_and_keep_tail(source,tmp_path):
    out=tmp_path/"fx.wav"
    audio.effects(source,out,Effects(reverb=.2,delay=.1).model_dump(),120)
    result=audio.load(out)
    assert len(result)==4*48000 and np.isfinite(result).all()
    assert np.max(np.abs(result[-1]))<1e-7


@pytest.mark.parametrize("format",["wav","flac","mp3"])
def test_two_pass_mastering_is_measured(tmp_path,format):
    src=tmp_path/"music.wav";audio.write(src,sine(4))
    out=tmp_path/f"master.{format}"
    report=audio.master(src,out,-14,format)
    assert out.stat().st_size>1000 and not report["silent"]
    measured=audio.loudness(out)
    assert abs(float(measured["input_i"])+14)<.5
    if format!="mp3":assert float(measured["input_tp"])<=-.8
    if format=="wav":assert sf.info(out).subtype=="PCM_24"


def test_silence_master_does_not_crash(tmp_path):
    src=tmp_path/"silence.wav";audio.write(src,np.zeros((48000,2),np.float32))
    out=tmp_path/"master.wav";report=audio.master(src,out)
    assert report["silent"] and not audio.load(out).any()


def test_invalid_audio_rejected(tmp_path):
    src=tmp_path/"fake.mp3";src.write_text("not audio")
    with pytest.raises(ValueError):audio.decode(src,tmp_path/"decoded.wav")


def test_lrc_multiple_timestamps_and_offsets():
    lines=audio.parse_lrc("[ar:example]\n[offset:-100]\n[00:01.20][00:03.400]hello\n[00:04]world")
    assert lines==[{"time":1.1,"text":"hello"},{"time":3.3,"text":"hello"},{"time":3.9,"text":"world"}]
