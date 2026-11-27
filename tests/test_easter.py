import cv2
import numpy as np

from palmcards import easter
from palmcards.gestures import Hand

# The user's right hand (MediaPipe: "Left" in the mirrored frame), index up, the others curled,
# the thumb tucked: the back to the camera (index knuckle right of the pinky's on screen).
BACK = [(640, 600), (665, 580), (680, 555), (685, 535), (675, 520),  # wrist, thumb
        (680, 505), (680, 470), (680, 440), (680, 410),  # index: straight up
        (655, 500), (650, 470), (645, 490), (648, 515),  # middle .. pinky: curled
        (630, 505), (625, 475), (622, 495), (625, 518),
        (605, 515), (602, 490), (600, 505), (603, 522)]


def hand(points, flip=False):
    p = np.array(points, np.float32)
    if flip:  # the same hand turned round: the palm to the camera, as everyone browses
        p[:, 0] = 1280 - p[:, 0]
    return Hand(p, handedness="Left")


def test_the_sign_is_the_back_of_the_hand_with_the_index_up():
    assert easter.is_friend_sign(hand(BACK))
    assert not easter.is_friend_sign(hand(BACK, flip=True))  # the palm: browsing, not the sign


def test_held_it_shows_the_picture_once_and_fades(tmp_path):
    picture = tmp_path / "friend.jpg"
    cv2.imwrite(str(picture), np.full((90, 160, 3), 255, np.uint8))
    friend = easter.Friend(picture)
    fired = [t / 10 for t in range(30) if friend.update([hand(BACK)], t / 10)]
    assert fired == [easter.HOLD_S]  # once per hold, however long it is held
    start = fired[0]
    frame = np.zeros((72, 128, 3), np.uint8)
    friend.draw(frame, start + 1.0)
    assert (frame[36, 64] == 255).all()  # shown in full
    assert friend.alpha(start + easter.SHOW_S + 0.1) == 0.0  # and gone again
    turned = easter.Friend(picture)
    assert not any(turned.update([hand(BACK, flip=True)], t / 10) for t in range(30))
    assert not any(easter.Friend(tmp_path / "missing.jpg").update([hand(BACK)], t / 10) for t in range(30))
