"""Pydantic models for MWBO Gebiete/Zusatz-Weiterbildung."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Inhalt(BaseModel):
    """A single row from the Weiterbildungsinhalte competency table (3-column)."""

    abschnitt: str  # Section heading, e.g. "Notfälle", "Diagnostische Verfahren"
    kognitive_und_methodenkompetenz: str | None = None
    handlungskompetenz: str | None = None
    richtzahl: int | None = None


class Kursinhalt(BaseModel):
    """A single item from a Kurs-Weiterbildung content table (1-column)."""

    abschnitt: str  # Section heading, e.g. "Kursinhalte (180 Stunden)"
    text: str


class Weiterbildung(BaseModel):
    """A Facharzt, Schwerpunkt, or Zusatz-Weiterbildung section from the MWBO."""

    typ: Literal["facharzt", "schwerpunkt", "zusatz-weiterbildung"]
    gebiet: str  # e.g. "Allgemeinmedizin", "Chirurgie", "Akupunktur"
    bezeichnung: str  # e.g. "Facharzt/Fachärztin für Allgemeinmedizin"
    zusatzbezeichnung: str | None = None  # e.g. "Hausarzt/Hausärztin"
    gebietsdefinition: str | None = None  # Facharzt only
    definition: str | None = None  # Zusatz-Weiterbildung only
    voraussetzung: str | None = None  # Schwerpunkt: "baut auf ... auf"
    mindestanforderungen: str | None = None  # Zusatz-Weiterbildung only
    weiterbildungszeit: str | None = None
    inhalte: list[Inhalt | Kursinhalt] = []
