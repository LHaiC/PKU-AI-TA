import pytest
from unittest.mock import MagicMock, patch

from auth.iaaa import get_session


class TestGetSession:
    def test_raises_without_credentials(self):
        with patch("auth.iaaa.settings") as mock_settings:
            mock_settings.pku_username = ""
            mock_settings.pku_password = ""
            mock_settings.bb_cookie = ""
            with pytest.raises(RuntimeError, match="PKU_USERNAME"):
                get_session()

    def test_raises_on_iaaa_failure(self):
        with patch("auth.iaaa.settings") as mock_settings:
            mock_settings.pku_username = "user"
            mock_settings.pku_password = "pass"
            mock_settings.bb_cookie = ""

            with patch("auth.iaaa.httpx.Client") as MockClient:
                mock_client = MagicMock()
                MockClient.return_value = mock_client

                # getPublicKey.do succeeds
                key_resp = MagicMock()
                key_resp.raise_for_status = MagicMock()
                key_resp.json.return_value = {
                    "success": True,
                    "key": (
                        "-----BEGIN PUBLIC KEY-----\n"
                        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqw9PsMk8v9ED/LiLT62I"
                        "DnelyIA/s8blyxqNmbgXT4xtq+Y64Bd+THYPZ4dUIRuFmMvPowQm9wL27W3PEtQy"
                        "C8VN+TzW/nPzc74fy9cRxgaSh1FXNQBqYZtltb6G5YvwBvZlYdKhE3Oo3noUD0FJ"
                        "JC11Nmcy2/x1V2pwXHRy2DHKaWB1EEtQ9dRxuMZolZIpEwWnT4CHfwEvth83kNRp"
                        "E8471KJEqyQqmqJt3JRerH4X4p41zQFIxCsrznAwku3b1qm0vgGLQ8t7XEiCjDX0"
                        "m5yIJEuW5t1YcteutuJX5+5oXxe2Fo04Wkn1pO6+QoJopqHcHJD5C+7GlnPOLB1c"
                        "DQIDAQAB\n"
                        "-----END PUBLIC KEY-----"
                    ),
                }

                # oauthlogin.do fails
                fail_resp = MagicMock()
                fail_resp.raise_for_status = MagicMock()
                fail_resp.json.return_value = {"success": False, "errors": "Invalid password"}

                mock_client.get.return_value = key_resp
                mock_client.post.return_value = fail_resp

                with pytest.raises(RuntimeError, match="IAAA login failed"):
                    get_session()

    def test_returns_client_on_success(self):
        with patch("auth.iaaa.settings") as mock_settings:
            mock_settings.pku_username = "user"
            mock_settings.pku_password = "pass"
            mock_settings.bb_cookie = ""

            with patch("auth.iaaa.httpx.Client") as MockClient:
                mock_client = MagicMock()
                MockClient.return_value = mock_client

                key_resp = MagicMock()
                key_resp.raise_for_status = MagicMock()
                key_resp.json.return_value = {
                    "success": True,
                    "key": (
                        "-----BEGIN PUBLIC KEY-----\n"
                        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqw9PsMk8v9ED/LiLT62I"
                        "DnelyIA/s8blyxqNmbgXT4xtq+Y64Bd+THYPZ4dUIRuFmMvPowQm9wL27W3PEtQy"
                        "C8VN+TzW/nPzc74fy9cRxgaSh1FXNQBqYZtltb6G5YvwBvZlYdKhE3Oo3noUD0FJ"
                        "JC11Nmcy2/x1V2pwXHRy2DHKaWB1EEtQ9dRxuMZolZIpEwWnT4CHfwEvth83kNRp"
                        "E8471KJEqyQqmqJt3JRerH4X4p41zQFIxCsrznAwku3b1qm0vgGLQ8t7XEiCjDX0"
                        "m5yIJEuW5t1YcteutuJX5+5oXxe2Fo04Wkn1pO6+QoJopqHcHJD5C+7GlnPOLB1c"
                        "DQIDAQAB\n"
                        "-----END PUBLIC KEY-----"
                    ),
                }

                token_resp = MagicMock()
                token_resp.raise_for_status = MagicMock()
                token_resp.json.return_value = {"success": True, "token": "abc123"}

                campus_resp = MagicMock()
                campus_resp.raise_for_status = MagicMock()

                # get() called twice: getPublicKey.do, then campusLogin
                mock_client.get.side_effect = [key_resp, campus_resp]
                mock_client.post.return_value = token_resp

                result = get_session()
                assert result is mock_client

                # Verify campusLogin was called with the token
                campus_call = mock_client.get.call_args_list[1]
                assert "abc123" in str(campus_call)
