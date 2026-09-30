"""Language registry for SautiPay.

Manages available languages and their metadata.
Each language module can contain vocabulary, phrases, intents, translations,
variations, code-switching examples, common expressions, and domain terminology.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LanguageModule:
    """Metadata for a supported language."""

    code: str
    name: str
    native_name: str
    is_active: bool = True
    has_verified_data: bool = False
    description: str = ""
    data_files: list = field(default_factory=list)


class LanguageRegistry:
    """Registry of all supported languages in SautiPay."""

    def __init__(self):
        self._languages: dict[str, LanguageModule] = {}
        self._register_default_languages()

    def _register_default_languages(self):
        """Register the default set of languages."""
        # English - fully supported
        self.register(
            LanguageModule(
                code="en",
                name="English",
                native_name="English",
                is_active=True,
                has_verified_data=True,
                description="English language module with full vocabulary and intents.",
            )
        )

        # Kiswahili - fully supported
        self.register(
            LanguageModule(
                code="sw",
                name="Kiswahili",
                native_name="Kiswahili",
                is_active=True,
                has_verified_data=True,
                description="Kiswahili language module with full vocabulary and intents.",
            )
        )

        # Other Kenyan languages - extensible stubs
        # These exist as placeholders for future verified datasets
        stub_languages = [
            ("luo", "Luo", "Luo"),
            ("kikuyu", "Kikuyu", "Gĩkũyũ"),
            ("kamba", "Kamba", "Kamba"),
            ("kisii", "Kisii", "Kisii"),
            ("meru", "Meru", "Mĩrũ"),
            ("samburu", "Samburu", "Samburu"),
        ]

        for code, name, native in stub_languages:
            self.register(
                LanguageModule(
                    code=code,
                    name=name,
                    native_name=native,
                    is_active=True,
                    has_verified_data=False,
                    description=(
                        f"{name} language module. "
                        "Awaiting verified native-speaker datasets."
                    ),
                )
            )

    def register(self, language: LanguageModule) -> None:
        """Register a language module."""
        self._languages[language.code] = language

    def get(self, code: str) -> Optional[LanguageModule]:
        """Get a language module by code."""
        return self._languages.get(code)

    def get_all(self, active_only: bool = True) -> list[LanguageModule]:
        """Get all registered languages.

        Args:
            active_only: If True, return only active languages.

        Returns:
            List of LanguageModule instances.
        """
        if active_only:
            return [lang for lang in self._languages.values() if lang.is_active]
        return list(self._languages.values())

    def is_supported(self, code: str) -> bool:
        """Check if a language code is supported."""
        return code in self._languages

    def is_verified(self, code: str) -> bool:
        """Check if a language has verified data."""
        lang = self._languages.get(code)
        return lang is not None and lang.has_verified_data