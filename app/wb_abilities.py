"""What the assistant can do, as abilities you switch on and off, and the router that keeps each turn small.

Every ability is a group of tools with a short paragraph of instructions and the words that call for it.
Only switched-on abilities are ever offered. And of those, a turn gets only the groups its words call for
(plus the groups the previous turn used, for follow-ups such as "make it longer"): a question about the TV
sends the four TV tools and two lines about them, not all forty tools and a page of instructions. If the
model then says it can't do something, the turn is asked again once with every switched-on ability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from wb_settings import Settings


@dataclass
class Ability:
    key: str
    label: str                 # shown in the Abilities list
    about: str                 # its tooltip
    groups: tuple              # tool groups it switches on
    panel: str = ""            # the panel "Open" shows (a key of the window's panels), "" = none
    page: str = ""             # a page inside that panel (Create: Picture, Music, ...)


@dataclass
class Group:
    key: str
    tools: tuple               # tool names (looked up in the assistant's TOOLS)
    prompt: str                # instructions sent only when the group is
    words: tuple = ()          # words that call for it (whole words; English and Swedish)
    always: bool = False       # sent every turn (small and often needed)
    extra: dict = field(default_factory=dict)


# ---- tool groups --------------------------------------------------------------------------------

GROUPS = {g.key: g for g in (
    Group("expert", ("ask_expert",),
          "For code, maths or logic problems, or long writing (letters, translations), use ask_expert and pass on "
          "its answer; say which expert answered.", always=True),
    Group("web", ("web_search", "read_page", "read_my_cv"),
          "You CAN search the web: web_search, then read_page on the best results, for anything current (news, "
          "prices, weather, job listings); say which site it came from. read_my_cv reads the user's CV: for job "
          "hunting read it first, then search for matching jobs and name the best two or three and why they fit.",
          ("search", "web", "google", "news", "latest", "today", "current", "currently", "price", "prices",
           "weather", "job", "jobs", "hiring", "vacancy", "cv", "resume", "online", "internet", "website says",
           "who won", "sök", "söka", "nyheter", "senaste", "idag", "pris", "väder", "jobb", "tjänst")),
    Group("research", ("research_report",),
          "research_report writes a full cited report from the web (it takes a few minutes and opens by itself "
          "when ready). Use it when the user asks for research or a report, not for a quick fact.",
          ("research", "report", "investigate", "in depth", "in-depth", "thorough", "sources", "study",
           "undersök", "undersöka", "rapport", "utred", "fördjupa")),
    Group("pc", ("pc_info", "list_folder", "find_files", "read_text_file", "open_program", "media"),
          "You CAN look at this PC (read-only): pc_info (name, IP, uptime, CPU, memory, disk), list_folder, "
          "find_files, read_text_file; open_program starts allowed programs; media plays, pauses or skips. Use "
          "these instead of telling the user to run commands; you run no terminal commands, by design.",
          ("pc", "computer", "laptop", "folder", "folders", "file", "files", "downloads", "desktop", "documents",
           "ip", "uptime", "cpu", "memory", "ram", "disk", "drive", "space", "open", "start", "launch", "program",
           "app", "chrome", "steam", "discord", "pause", "resume", "skip", "next track", "dator", "datorn", "mapp",
           "mappen", "fil", "filer", "filen", "öppna", "starta", "hämtade filer", "skrivbordet")),
    Group("homelab", ("check_status", "restart_container"),
          "check_status tells whether the user's services are up; restart_container restarts one (asks first).",
          ("status", "server", "servers", "nas", "pi", "raspberry", "container", "containers", "restart",
           "is it up", "is it down", "service", "services", "pihole", "pi-hole", "vikunja", "minecraft",
           "home lab", "homelab", "tjänst", "tjänster", "starta om", "nere", "igång")),
    Group("tasks", ("open_tasks", "add_task", "complete_task"),
          "open_tasks reads the user's Vikunja to-do list; add_task adds one (asks first); complete_task "
          "finishes one (asks first).",
          ("task", "tasks", "todo", "to-do", "to do", "remind", "reminder", "vikunja", "list", "done", "finish",
           "uppgift", "uppgifter", "att göra", "påminn", "klar")),
    Group("brain", ("remember", "recall"),
          "remember saves something the user wants kept; recall looks in the user's notes.",
          ("remember", "recall", "forget", "note", "notes", "memory", "do you know", "what do you know",
           "kom ihåg", "minns", "anteckning", "anteckningar", "minne")),
    Group("pictures", ("create_image",),
          "create_image paints a picture from a description and opens it; say who made it.",
          ("picture", "pictures", "image", "images", "paint", "draw", "drawing", "photo", "illustration",
           "logo", "wallpaper", "bild", "bilder", "måla", "rita", "teckning", "foto")),
    Group("music", ("create_music",),
          "create_music makes a short piece of music (no singing) and plays it; say who made it.",
          ("music", "tune", "melody", "beat", "instrumental", "soundtrack", "jingle", "compose", "musik",
           "melodi", "komponera")),
    Group("songs", ("make_song",),
          "make_song writes lyrics and has them sung (takes a minute or two, plays by itself when ready).",
          ("song", "songs", "sing", "sung", "lyrics", "vocals", "singer", "sång", "sånger", "låt", "låtar",
           "sjung", "sjunga", "sångtext")),
    Group("movies", ("make_movie",),
          "make_movie makes a short narrated movie (takes minutes, opens by itself when ready).",
          ("movie", "movies", "film", "films", "video", "animation", "animated", "trailer", "filmen")),
    Group("stories", ("write_story",),
          "write_story writes a short story, saves and opens it; then give its title and one sentence about it.",
          ("story", "stories", "tale", "fairy tale", "bedtime", "fable", "saga", "sagor", "berättelse",
           "historia", "novell")),
    Group("code", ("make_code_project", "change_code_project", "open_in_vs_code", "explain_project"),
          "You CAN create files and code: make_code_project writes a whole project (website, program, script) "
          "into the user's work folder and opens it in VS Code; change_code_project changes one; open_in_vs_code "
          "opens one; explain_project explains one. Never say you can't create files.",
          ("code", "coding", "program", "programming", "script", "website", "web site", "web page", "webpage",
           "web app", "app", "project", "projects", "vs code", "visual studio", "html", "css", "javascript",
           "python", "c#", "react", "game", "bug", "function", "repository", "repo", "kod", "koda", "program",
           "programmera", "hemsida", "webbsida", "projekt", "spel", "skript")),
    Group("invoices", ("process_invoices", "invoice_summary"),
          "process_invoices reads the invoices waiting in the inbox into checked CSV rows; invoice_summary says "
          "what has been read and what needs review.",
          ("invoice", "invoices", "receipt", "receipts", "bill", "bills", "vat", "csv", "faktura", "fakturor",
           "kvitto", "kvitton", "moms")),
    Group("documents", ("ask_my_documents",),
          "ask_my_documents answers from the user's own documents (PDFs and notes added to Documents), with "
          "sources; use it when the user asks about their documents, handbook, manuals or files they added.",
          ("document", "documents", "pdf", "pdfs", "handbook", "manual", "manuals", "policy", "contract",
           "my files", "my notes", "according to", "dokument", "dokumenten", "handbok", "manual", "avtal",
           "policy")),
    Group("tv", ("tv_power", "tv_watch", "tv_volume", "tv_status"),
          "tv_power, tv_watch (a favourite, app or channel), tv_volume and tv_status run the user's TV.",
          ("tv", "telly", "television", "channel", "channels", "cnn", "netflix", "youtube", "watch", "volume",
           "louder", "quieter", "mute", "kanal", "kanalen", "teve", "tvn", "tv:n", "titta", "volymen", "högre",
           "lägre")),
    Group("spotify", ("spotify_play", "spotify_control", "spotify_volume", "spotify_now_playing"),
          "spotify_play, spotify_control, spotify_volume and spotify_now_playing run Spotify (asks first).",
          ("spotify", "play", "playlist", "album", "artist", "song playing", "what's playing", "next song",
           "previous song", "skip", "pause", "spela", "spellista", "nästa låt", "vad spelas")),
)}


# ---- abilities you switch on and off ------------------------------------------------------------

ABILITIES = {a.key: a for a in (
    Ability("pictures", "Pictures", "Paint pictures (online image AI or SDXL-Turbo here)", ("pictures",),
            "create", "Picture"),
    Ability("music", "Music", "Make music with MusicGen here", ("music",), "create", "Music"),
    Ability("songs", "Songs", "Songs with singing: lyrics by the writing expert, sung by ACE-Step", ("songs",),
            "create", "Song"),
    Ability("movies", "Movies", "Short narrated movies: painted, animated, narrated and scored", ("movies",),
            "create", "Movie"),
    Ability("stories", "Stories", "Short stories by the writing expert", ("stories",), "create", "Story"),
    Ability("code", "Code", "New projects, changes and explanations, opened in VS Code", ("code",), "code"),
    Ability("research", "Web and research", "Web search, reading pages, your CV, and full cited reports",
            ("web", "research"), "research"),
    Ability("invoices", "Invoices", "Invoice PDFs and photos into checked CSV rows", ("invoices",), "invoices"),
    Ability("documents", "Documents", "Answers from your own PDFs and notes, with sources", ("documents",),
            "documents"),
    Ability("tv", "My TV", "Power, channels, apps and volume on your TV", ("tv",), "tv"),
    Ability("spotify", "Spotify", "Play, pause and skip on Spotify (needs Premium)", ("spotify",)),
    Ability("brain", "Brain", "Remembers what you tell it, follows your rules", ("brain",), "brain"),
    Ability("pc", "PC and home lab", "Look at this PC, open allowed programs, media keys, your services and "
            "Vikunja tasks", ("pc", "homelab", "tasks"), "access"),
)}

SPEAK = {"sv": "(talat; svara på svenska)", "en": "(spoken; reply in English)"}
TYPE = {"sv": "(skrivet; svara på svenska)", "en": "(typed; reply in English)"}


def enabled(s: Settings) -> list[str]:
    """Ability keys switched on. New abilities (not yet in the settings) start on."""
    chosen = s.abilities or {}
    return [k for k in ABILITIES if chosen.get(k, True)]


def ready(s: Settings, key: str, connected_spotify: bool = False) -> tuple[bool, str]:
    """Whether an ability can work now, and if not, why (shown greyed out in the list)."""
    if key == "tv" and not s.tv_ip:
        return False, "No TV set up yet: open it and use Find TV"
    if key == "spotify" and not connected_spotify:
        return False, "Not connected: Settings → Spotify"
    return True, ""


def _has(text: str, word: str) -> bool:
    if re.fullmatch(r"[\w\s'-]+", word):
        return re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", text) is not None
    return word in text


def groups_for(ability_keys: list[str]) -> list[str]:
    out = []
    for k in ability_keys:
        out += [g for g in ABILITIES[k].groups if g not in out]
    return out


@dataclass
class Route:
    groups: list[str]          # tool groups offered this turn
    why: dict                  # group -> the words that called for it, or "follow-up" / "always"
    all_groups: list[str]      # every switched-on group (used once if the model says it can't)


def pick(text: str, on_groups: list[str], previous: list[str] | None = None) -> Route:
    """The tool groups this message needs, out of the switched-on ones."""
    low = text.lower()
    why: dict = {}
    for key in on_groups + ["expert"]:
        g = GROUPS[key]
        if g.always:
            why[key] = "always"
            continue
        hits = [w for w in g.words if _has(low, w)]
        if hits:
            why[key] = ", ".join(f"“{w}”" for w in hits[:3])
    for key in previous or []:  # "make it longer", "and the other one?": keep what the last turn used
        if key in on_groups and key not in why:
            why[key] = "follow-up to the last answer"
    chosen = [k for k in ["expert"] + on_groups if k in why]
    return Route(chosen, why, ["expert"] + [k for k in on_groups if k != "expert"])


def tools_for(groups: list[str], all_tools: dict) -> dict:
    names = [t for g in groups for t in GROUPS[g].tools]
    return {n: all_tools[n] for n in names if n in all_tools}


def prompt_for(groups: list[str]) -> str:
    return "\n".join(GROUPS[g].prompt for g in groups if g in GROUPS)


def group_of_tool(name: str) -> str | None:
    return next((g.key for g in GROUPS.values() if name in g.tools), None)
