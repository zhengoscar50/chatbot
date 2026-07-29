import pytest
from app.models.schemas import validate_username, RegisterRequest


def test_valid_usernames_pass():
    assert validate_username("Oscar.Zheng") == "Oscar.Zheng"
    assert validate_username("a_b-1") == "a_b-1"


@pytest.mark.parametrize("bad", ["Oscar Zheng", "ab", "___", "a"*33, "no!bang"])
def test_bad_usernames_friendly_message(bad):
    with pytest.raises(ValueError) as e:
        validate_username(bad)
    assert "letters, numbers" in str(e.value)


def test_register_request_rejects_space_with_friendly_message():
    with pytest.raises(ValueError) as e:
        RegisterRequest(username="Oscar Zheng", password="password123")
    assert "letters, numbers" in str(e.value)
