from utils.dingtalk_notifier import DingTalkNotifier


def test_failed_dingtalk_image_is_retained_for_recovery(tmp_path):
    image = tmp_path / "too-large.png"
    image.write_bytes(b"x" * (12 * 1024 + 1))
    notifier = DingTalkNotifier(webhook_url="https://example.invalid/webhook")

    assert notifier.send_image(str(image)) is False
    assert image.exists()


def test_successful_dingtalk_image_is_removed(monkeypatch, tmp_path):
    image = tmp_path / "small.png"
    image.write_bytes(b"png")
    notifier = DingTalkNotifier(webhook_url="https://example.invalid/webhook")
    monkeypatch.setattr(notifier, "_send_request", lambda payload: True)

    assert notifier.send_image(str(image)) is True
    assert not image.exists()
