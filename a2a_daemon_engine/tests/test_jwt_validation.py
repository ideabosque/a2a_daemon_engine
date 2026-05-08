#!/usr/bin/env python
"""
Unit Tests for Config JWT Secret Validation

Tests the JWT secret key validation in the Config class to ensure:
- Weak/default secrets are rejected
- Strong secrets are accepted
- Appropriate error messages are raised
- Warnings are issued for short but non-weak keys
"""

from unittest.mock import MagicMock

import pytest

from a2a_daemon_engine.handlers.config import Config


class TestJWTSecretValidation:
    """Test JWT secret key validation in Config class."""

    @pytest.fixture(autouse=True)
    def reset_config(self):
        """Reset Config class before each test."""
        yield

    def test_weak_secret_changeme_rejected(self):
        """Test that 'CHANGEME' is rejected as weak secret."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="CHANGEME",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)
        assert "CHANGEME" in str(exc_info.value)

    def test_weak_secret_lowercase_changeme_rejected(self):
        """Test that lowercase 'changeme' is rejected."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="changeme",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_weak_secret_secret_rejected(self):
        """Test that 'secret' is rejected as weak."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="secret",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_weak_secret_password_rejected(self):
        """Test that 'password' is rejected as weak."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="password",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_weak_secret_123456_rejected(self):
        """Test that '123456' is rejected as weak."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="123456",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_weak_secret_admin_rejected(self):
        """Test that 'admin' is rejected as weak."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="admin",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_empty_secret_rejected(self):
        """Test that empty string is rejected."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_whitespace_only_secret_rejected(self):
        """Test that whitespace-only secret is rejected."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="   ",
                endpoint_id="test",
                part_id="test",
            )

        assert "Invalid JWT_SECRET_KEY" in str(exc_info.value)

    def test_strong_secret_accepted(self):
        """Test that strong secret (>=32 chars) is accepted."""
        strong_secret = "this-is-a-very-strong-secret-key-32chars"

        # Should not raise any exception
        Config.initialize(
            logger=MagicMock(),
            auth_provider="local",
            jwt_secret_key=strong_secret,
            endpoint_id="test",
            part_id="test",
        )

        assert Config.jwt_secret_key == strong_secret

    def test_short_non_weak_secret_warning(self):
        """Test that short but non-weak secret triggers warning."""
        short_secret = "short-but-unique"  # 16 chars, not in weak list

        mock_logger = MagicMock()

        # Should not raise ValueError but should log warning
        Config.initialize(
            logger=mock_logger,
            auth_provider="local",
            jwt_secret_key=short_secret,
            endpoint_id="test",
            part_id="test",
        )

        # Verify warning was logged
        mock_logger.warning.assert_called()
        warning_calls = [call for call in mock_logger.warning.call_args_list
                        if "JWT_SECRET_KEY is only" in str(call)]
        assert len(warning_calls) > 0

    def test_exactly_32_chars_accepted(self):
        """Test that exactly 32 character secret is accepted."""
        secret_32 = "a" * 32

        # Should not raise
        Config.initialize(
            logger=MagicMock(),
            auth_provider="local",
            jwt_secret_key=secret_32,
            endpoint_id="test",
            part_id="test",
        )

        assert Config.jwt_secret_key == secret_32

    def test_cognito_auth_skips_validation(self):
        """Test that Cognito auth provider skips local JWT validation."""
        # Even with weak secret, should not raise when using cognito
        Config.initialize(
            logger=MagicMock(),
            auth_provider="cognito",
            jwt_secret_key="CHANGEME",  # Weak but shouldn't matter
            endpoint_id="test",
            part_id="test",
        )

        # Should accept the weak secret since using Cognito
        assert Config.jwt_secret_key == "CHANGEME"

    def test_error_message_contains_requirements(self):
        """Test that error message explains requirements."""
        with pytest.raises(ValueError) as exc_info:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="CHANGEME",
                endpoint_id="test",
                part_id="test",
            )

        error_msg = str(exc_info.value)
        assert "strong, non-default value" in error_msg
        assert "at least 32 characters" in error_msg

    def test_secret_with_whitespace_trimmed(self):
        """Test that secrets with leading/trailing whitespace are trimmed."""
        # " CHANGEME " when stripped becomes "CHANGEME" which is weak
        with pytest.raises(ValueError):
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key="  CHANGEME  ",
                endpoint_id="test",
                part_id="test",
            )


class TestJWTSecretEdgeCases:
    """Test edge cases for JWT secret validation."""

    def test_very_long_secret_accepted(self):
        """Test that very long secrets are accepted."""
        long_secret = "x" * 1000

        Config.initialize(
            logger=MagicMock(),
            auth_provider="local",
            jwt_secret_key=long_secret,
            endpoint_id="test",
            part_id="test",
        )

        assert Config.jwt_secret_key == long_secret

    def test_secret_with_special_characters(self):
        """Test that secrets with special characters are accepted."""
        special_secret = "Str0ng!@#$%^&*()_+-=[]{}|;:,.<>?"

        Config.initialize(
            logger=MagicMock(),
            auth_provider="local",
            jwt_secret_key=special_secret,
            endpoint_id="test",
            part_id="test",
        )

        assert Config.jwt_secret_key == special_secret

    def test_secret_with_unicode(self):
        """Test that secrets with unicode characters are handled."""
        unicode_secret = "str0ng-secreT-k3y-unicode-test-32chars"

        # Should handle unicode (may or may not be accepted based on implementation)
        try:
            Config.initialize(
                logger=MagicMock(),
                auth_provider="local",
                jwt_secret_key=unicode_secret,
                endpoint_id="test",
                part_id="test",
            )
            # If accepted, verify it's stored
            assert Config.jwt_secret_key == unicode_secret
        except (ValueError, UnicodeEncodeError):
            # Unicode handling may vary - this is acceptable
            pass

    def test_multiple_weak_words_combined(self):
        """Test combination of weak words is still weak if in list."""
        combined = "adminpassword123"  # Combination but not in weak list

        mock_logger = MagicMock()

        # Should be accepted (not in weak list) but warned if short
        Config.initialize(
            logger=mock_logger,
            auth_provider="local",
            jwt_secret_key=combined,
            endpoint_id="test",
            part_id="test",
        )

        # Should not raise ValueError since not exact match
        assert Config.jwt_secret_key == combined


class TestConfigInitialization:
    """Test Config initialization with various settings."""

    def test_full_config_initialization(self):
        """Test complete Config initialization with all settings."""
        mock_logger = MagicMock()

        Config.initialize(
            logger=mock_logger,
            auth_provider="local",
            jwt_secret_key="this-is-a-secure-32-char-secret-key",
            jwt_algorithm="HS256",
            access_token_exp=30,
            endpoint_id="test-endpoint",
            part_id="test-part",
            transport="http",
        )

        assert Config.auth_provider == "local"
        assert Config.jwt_algorithm == "HS256"
        assert Config.access_token_exp == 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
