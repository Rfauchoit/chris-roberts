"""Une icône dans la zone de notification, en ctypes pur.

Demande de l'utilisateur (2026-08-10) : réduire doit mettre le programme
dans la barre système, pas dans la barre des tâches — pour le lanceur comme
pour le compagnon. Les deux tournent en tâche de fond des heures durant
pendant qu'on joue ; occuper une case de la barre des tâches pour ça est du
gaspillage, et fermer la fenêtre ne doit pas arrêter la collecte.

**Pourquoi pas `pystray`.** Il dépend de Pillow, que ce projet n'a
délibérément pas — c'est déjà la raison pour laquelle l'icône du lanceur
est produite par un script maison. Une dépendance de plus pour une icône
de 16 pixels serait payée à chaque installation.

**Comment ça tient debout.** Une icône de notification appartient à une
fenêtre, et Windows lui envoie les clics par un message. Tk possède déjà sa
propre fenêtre et sa propre boucle, mais on ne peut pas y greffer un
gestionnaire sans sous-classer sa procédure — fragile. On crée donc une
fenêtre **message-only** dans un fil à part, avec sa propre boucle : elle ne
partage rien avec Tk et communique par appels, que l'appelant renvoie dans
sa file. C'est le même principe que les tâches du lanceur.
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as wt
import pathlib
import threading
from typing import Callable

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_APP_ICONE = 0x0400 + 1
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000

ID_OUVRIR, ID_QUITTER = 1001, 1002


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
    ]


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


_PROC = ctypes.WINFUNCTYPE(ctypes.c_long, wt.HWND, wt.UINT,
                           wt.WPARAM, wt.LPARAM)

#: Fenêtre message-only : elle ne s'affiche jamais et ne reçoit que des
#: messages. C'est un `HWND` spécial, pas un entier — le passer tel quel
#: lève « int too long to convert » sur un Python 64 bits.
HWND_MESSAGE = wt.HWND(-3)


def _declarer(user32) -> None:
    """Prototypes des appels natifs.

    **Sans eux, ctypes suppose que tout rentre dans un `int`.** Sur un
    Windows 64 bits, un `HWND` est un pointeur de 64 bits : `CreateWindowExW`
    rendait un handle tronqué, et tout ce qui s'ensuivait échouait sans dire
    pourquoi. Mesuré à l'écriture, le 2026-08-10.
    """
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.DefWindowProcW.restype = ctypes.c_long
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                                  ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.LoadIconW.restype = wt.HICON
    user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.DestroyWindow.argtypes = [wt.HWND]


class Disponible(Exception):
    """La barre système n'est pas utilisable ici — hors Windows, ou API absente."""


class Icone:
    """Une icône de notification, avec un menu à deux entrées.

    `sur_ouvrir` est appelée depuis le **fil de l'icône**, jamais depuis
    celui de l'interface : l'appelant doit reposter dans sa propre file.
    Tkinter n'est pas thread-safe, et c'est le genre de détail qui produit
    un plantage une fois sur cinquante.
    """

    def __init__(self, titre: str, icone: pathlib.Path | None,
                 sur_ouvrir: Callable[[], None],
                 sur_quitter: Callable[[], None]):
        if not hasattr(ctypes, "windll"):
            raise Disponible("pas de Windows")
        self.titre = titre[:127]
        self.icone = icone
        self.sur_ouvrir = sur_ouvrir
        self.sur_quitter = sur_quitter
        self._hwnd = None
        self._pret = threading.Event()
        self._echec: Exception | None = None
        self._fil = threading.Thread(target=self._servir, daemon=True)
        self._fil.start()
        self._pret.wait(5)
        if self._echec is not None:
            raise Disponible(str(self._echec))
        # **La boucle native doit mourir avant l'interpréteur.** Un fil
        # démon bloqué dans `GetMessageW` est encore vivant quand Python
        # démonte ses structures : Windows appelle alors notre procédure
        # dans un interpréteur à moitié détruit, et le processus part en
        # violation d'accès. Trace observée au premier test, le 2026-08-10 —
        # les tests passaient, et le crash s'imprimait juste après.
        atexit.register(self.retirer)

    # ------------------------------------------------------------- interne

    def _servir(self) -> None:
        try:
            user32 = ctypes.windll.user32
            _declarer(user32)
            # La procédure doit rester référencée : ramassée par le GC, le
            # premier clic ferait planter le processus dans du code natif.
            self._proc = _PROC(self._traiter)
            classe = _WNDCLASS()
            classe.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
            classe.lpszClassName = f"CCC_Tray_{id(self)}"
            classe.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
            atome = user32.RegisterClassW(ctypes.byref(classe))
            if not atome:
                raise OSError("RegisterClassW a échoué")
            # HWND_MESSAGE (-3) : une fenêtre qui ne s'affiche jamais et ne
            # reçoit que des messages. C'est exactement ce qu'on veut.
            self._hwnd = user32.CreateWindowExW(
                0, classe.lpszClassName, self.titre, 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, classe.hInstance, None)
            if not self._hwnd:
                raise OSError("CreateWindowExW a échoué")
            self._poser()
        except Exception as exc:                              # noqa: BLE001
            self._echec = exc
            self._pret.set()
            return
        self._pret.set()

        message = wt.MSG()
        while ctypes.windll.user32.GetMessageW(
                ctypes.byref(message), None, 0, 0) > 0:
            ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))

    def _charger_icone(self):
        if self.icone is not None and pathlib.Path(self.icone).exists():
            handle = ctypes.windll.user32.LoadImageW(
                None, str(pathlib.Path(self.icone).resolve()), IMAGE_ICON,
                0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if handle:
                return handle
        # 32512 = IDI_APPLICATION. Une icône par défaut vaut mieux qu'aucune
        # icône : sans `hIcon`, Windows n'affiche rien du tout.
        return ctypes.windll.user32.LoadIconW(None, 32512)

    def _donnees(self) -> _NOTIFYICONDATA:
        donnees = _NOTIFYICONDATA()
        donnees.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
        donnees.hWnd = self._hwnd
        donnees.uID = 1
        donnees.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        donnees.uCallbackMessage = WM_APP_ICONE
        donnees.hIcon = self._charger_icone()
        donnees.szTip = self.titre
        return donnees

    def _poser(self) -> None:
        ctypes.windll.shell32.Shell_NotifyIconW(
            NIM_ADD, ctypes.byref(self._donnees()))

    def _traiter(self, hwnd, message, wparam, lparam) -> int:
        if message == WM_APP_ICONE:
            evenement = lparam & 0xFFFF
            if evenement in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._appeler(self.sur_ouvrir)
            elif evenement == WM_RBUTTONUP:
                self._menu()
            return 0
        if message == WM_COMMAND:
            if (wparam & 0xFFFF) == ID_OUVRIR:
                self._appeler(self.sur_ouvrir)
            elif (wparam & 0xFFFF) == ID_QUITTER:
                self._appeler(self.sur_quitter)
            return 0
        if message == WM_CLOSE:
            # **`DestroyWindow` n'est légal que depuis le fil qui a créé la
            # fenêtre.** Appelé d'ailleurs, il échoue en silence et la
            # boucle native ne sort jamais — le fil survit à l'interpréteur
            # et le processus part en violation d'accès à la fermeture.
            # On passe donc par un message, que le bon fil dépile.
            ctypes.windll.user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return ctypes.windll.user32.DefWindowProcW(
            hwnd, message, wparam, lparam)

    def _appeler(self, rappel: Callable[[], None]) -> None:
        try:
            rappel()
        except Exception:                                     # noqa: BLE001
            pass          # un rappel fautif ne doit pas tuer la boucle

    def _menu(self) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_OUVRIR, "Ouvrir")
        user32.AppendMenuW(menu, MF_STRING, ID_QUITTER, "Quitter")
        point = wt.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Sans `SetForegroundWindow`, le menu ne se referme pas quand on
        # clique ailleurs — comportement documenté de `TrackPopupMenu`.
        user32.SetForegroundWindow(self._hwnd)
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y,
                              0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

    # -------------------------------------------------------------- public

    def retirer(self) -> None:
        """Enlève l'icône. Sans ça, elle reste jusqu'au prochain survol."""
        if self._hwnd:
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(
                    NIM_DELETE, ctypes.byref(self._donnees()))
                # WM_CLOSE plutôt que `DestroyWindow` : c'est le fil
                # propriétaire qui doit détruire, et lui seul.
                ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:                                 # noqa: BLE001
                pass
            self._hwnd = None
            self._fil.join(timeout=2)
