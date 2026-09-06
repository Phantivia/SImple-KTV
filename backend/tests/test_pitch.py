import numpy as np
import pytest
from app import pitch, audio


def analysis(midi=60.3,seconds=2):
    times=np.arange(0,seconds,.01);m=np.ones(len(times))*midi
    return {"times":times,"midi":m,"f0":440*2**((m-69)/12),"confidence":np.ones(len(times)),"engine":"fixture"}


def test_scale_correction_harmony_and_manual_notes():
    a=analysis()
    corrected=pitch.correction(a,strength=1,retune_ms=0)
    np.testing.assert_allclose(corrected,60)
    manual=pitch.correction(a,strength=1,retune_ms=0,manual=[{"start":.5,"end":1,"midi":67}])
    np.testing.assert_allclose(manual[50:100],67)
    np.testing.assert_allclose(pitch.harmony_target(a,2),64+.3*.65)
    np.testing.assert_allclose(pitch.correction(a,strength=0),a["midi"])
    assert pitch.note_regions(a)==[{"start":0.,"end":2.,"midi":60.}]


def test_unvoiced_is_not_forced_into_notes():
    a=analysis();a["midi"][:20]=np.nan
    corrected=pitch.correction(a)
    assert np.isnan(corrected[:20]).all()
    compact=pitch.compact(a,corrected)
    assert compact["midi"][0] is None and compact["target"][0] is None
    a["midi"][:]=np.nan
    with pytest.raises(ValueError):pitch.correction(a)


def test_actual_pyin_tracks_synthetic_voice(source):
    result=pitch.analyze(source,"pyin")
    voiced=result["f0"][np.isfinite(result["f0"])]
    assert len(voiced)>100
    assert abs(np.median(voiced)-220)<3
    assert np.all(np.diff(result["times"])>0)


def test_actual_psola_changes_pitch_and_preserves_length(source,tmp_path):
    out=tmp_path/"tuned.wav"
    times=np.arange(0,2,.01)
    pitch.synthesize(source,out,times,np.full(len(times),60.))
    result=audio.load(out)
    assert result.shape==(96000,2) and np.isfinite(result).all()
    middle=result[24000:-24000].mean(axis=1)
    freq=np.fft.rfftfreq(len(middle),1/48000)[np.argmax(abs(np.fft.rfft(middle*np.hanning(len(middle)))))]
    assert abs(freq-261.626)<3
