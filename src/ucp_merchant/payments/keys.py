"""ECDSA P-256 key management for AP2 payment mandates.

Generates, stores, and exports elliptic-curve key pairs used to sign and
verify payment mandates.  Keys are kept in-memory and exported as JWK for
interoperability with UCP agents.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

import structlog
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from ucp_merchant.models import KeyPairInfo

logger = structlog.get_logger(__name__)


def _int_to_base64url(value: int, length: int) -> str:
    """Encode a positive integer as an unpadded base64url string."""
    raw = value.to_bytes(length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class KeyManager:
    """In-memory ECDSA P-256 key store.

    Each key pair is identified by a unique ``key_id`` and can be retrieved
    by that ID.  Public keys can be exported as JWK for distribution.
    """

    def __init__(self) -> None:
        self._keys: dict[str, ec.EllipticCurvePrivateKey] = {}
        self._info: dict[str, KeyPairInfo] = {}

    # -- public API ----------------------------------------------------------

    def generate_key_pair(self) -> KeyPairInfo:
        """Generate a new ECDSA P-256 key pair and store it.

        Returns
        -------
        KeyPairInfo
            Metadata including the ``key_id`` and public-key JWK.
        """
        key_id = f"key_{uuid.uuid4().hex[:12]}"
        private_key = ec.generate_private_key(ec.SECP256R1())
        self._keys[key_id] = private_key

        jwk = self._export_jwk(key_id, private_key)

        info = KeyPairInfo(
            key_id=key_id,
            algorithm="ES256",
            curve="P-256",
            created_at=datetime.utcnow(),
            public_key_jwk=jwk,
        )
        self._info[key_id] = info

        logger.info("key_pair_generated", key_id=key_id)
        return info

    def get_public_key_jwk(self, key_id: str) -> dict[str, str]:
        """Export the public key for *key_id* as a JWK dict.

        Raises
        ------
        KeyError
            If *key_id* does not exist.
        """
        private_key = self._get_private(key_id)
        return self._export_jwk(key_id, private_key)

    def get_private_key(self, key_id: str) -> ec.EllipticCurvePrivateKey:
        """Return the raw private key object for signing.

        Raises
        ------
        KeyError
            If *key_id* does not exist.
        """
        return self._get_private(key_id)

    def get_key_info(self, key_id: str) -> KeyPairInfo:
        """Return metadata about a key pair.

        Raises
        ------
        KeyError
            If *key_id* does not exist.
        """
        info = self._info.get(key_id)
        if info is None:
            raise KeyError(f"Key not found: {key_id!r}")
        return info

    def list_keys(self) -> list[KeyPairInfo]:
        """Return metadata for all stored key pairs."""
        return list(self._info.values())

    def get_public_key_pem(self, key_id: str) -> str:
        """Export the public key for *key_id* in PEM format.

        Raises
        ------
        KeyError
            If *key_id* does not exist.
        """
        private_key = self._get_private(key_id)
        public_key = private_key.public_key()
        pem_bytes = public_key.public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )
        return pem_bytes.decode("ascii")

    def export_private_key_pem(self, key_id: str) -> str:
        """Export the private key in PEM format (for test/demo only).

        Raises
        ------
        KeyError
            If *key_id* does not exist.
        """
        private_key = self._get_private(key_id)
        pem_bytes = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
        return pem_bytes.decode("ascii")

    # -- private helpers -----------------------------------------------------

    def _get_private(self, key_id: str) -> ec.EllipticCurvePrivateKey:
        key = self._keys.get(key_id)
        if key is None:
            raise KeyError(f"Key not found: {key_id!r}")
        return key

    @staticmethod
    def _export_jwk(
        key_id: str, private_key: ec.EllipticCurvePrivateKey
    ) -> dict[str, str]:
        """Build a JWK dict from an ECDSA P-256 public key."""
        public_key = private_key.public_key()
        public_numbers = public_key.public_numbers()

        # P-256 coordinates are 32 bytes each.
        x = _int_to_base64url(public_numbers.x, 32)
        y = _int_to_base64url(public_numbers.y, 32)

        return {
            "kty": "EC",
            "crv": "P-256",
            "x": x,
            "y": y,
            "kid": key_id,
            "use": "sig",
            "alg": "ES256",
        }
