from medialib import videoprobe


def test_video_bitrate_uses_language_suffixed_matroska_statistic():
    normalized = videoprobe._normalize_video(
        {
            "index": 0,
            "codec_type": "video",
            "tags": {
                "BPS-eng": "57537352",
                "NUMBER_OF_BYTES-eng": "30878657322",
                "language": "eng",
            },
        }
    )
    assert normalized["bit_rate"] == 57_537_352
    assert normalized["statistics_tags"] == {
        "BPS-eng": "57537352",
        "NUMBER_OF_BYTES-eng": "30878657322",
    }
    assert normalized["language"] == "eng"
