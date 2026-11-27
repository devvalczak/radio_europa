"""The functions of the app that use AI. Each can have its own connection (Ustawienia → AI); the rest follow the default.

A module's `ctx.ai(...)` uses the module id as its function (news, mail), so a module only needs an entry here when
it wants its own label; any other key simply follows the default."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Use:
    key: str
    label: str
    description: str
    private: bool = False  # works on private data (mail): "only local" is on until the user decides otherwise
    needs_tools: bool = False  # tool calling: the connection's model must support it


USES: tuple[Use, ...] = (
    Use("assistant", "Asystent (czat i plan dnia)", "Rozmowa, planowanie, dodawanie i zmiana wpisów. Model musi umieć wywoływać narzędzia.",
        needs_tools=True),
    Use("capture", "„Dodaj zdaniem”", "Zamienia jedno zdanie na zadanie, wydarzenie lub notatkę. Krótkie zadanie, wystarczy szybki model."),
    Use("autofill", "Uzupełnianie zadań", "Podpowiada priorytet i czas trwania nowego zadania. Krótkie zadanie, wystarczy szybki model."),
    Use("news", "Streszczenia newsów", "Zbiorcze streszczenie nagłówków z kanałów RSS."),
    Use("mail", "Streszczenia poczty", "Streszcza nieprzeczytane wiadomości. To dane prywatne: najlepiej lokalny model.", private=True),
    Use("test", "Test w Diagnostyce", "Sprawdza, czy AI odpowiada."),
)
BY_KEY = {u.key: u for u in USES}
DEFAULT = "default"
