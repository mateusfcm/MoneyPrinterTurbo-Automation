from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BLOG_URL = "https://www.padrejoaocarlos.com"
FEED_URL = f"{BLOG_URL}/feeds/posts/default"

PASTA_BASE = Path(__file__).resolve().parent
PASTA_DADOS = PASTA_BASE / "data"
ARQUIVO_POSTS = PASTA_DADOS / "posts_extraidos.json"

TIMEOUT = 30
MAX_POSTS = 10
FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def criar_sessao() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def carregar_json(caminho: Path, padrao: Any) -> Any:
    if not caminho.exists():
        return padrao

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (json.JSONDecodeError, OSError) as erro:
        logging.warning(
            "Não foi possível ler %s: %s",
            caminho,
            erro,
        )
        return padrao


def salvar_json(caminho: Path, dados: Any) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2,
        )

    temporario.replace(caminho)


def criar_id_post(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def normalizar_espacos(texto: str) -> str:
    texto = html.unescape(texto)
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def html_para_texto(conteudo_html: str) -> str:
    soup = BeautifulSoup(conteudo_html, "html.parser")

    for elemento in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "form",
            "button",
        ]
    ):
        elemento.decompose()

    texto = soup.get_text("\n")
    return normalizar_espacos(texto)


def localizar_url_post(entry: dict[str, Any]) -> str | None:
    for link in entry.get("link", []):
        if link.get("rel") == "alternate":
            return link.get("href")

    return None


def extrair_referencia_biblica(texto: str) -> str | None:
    padrao = re.compile(
        r"\b("
        r"Mt|Mc|Lc|Jo|At|Rm|1\s*Cor|2\s*Cor|Gl|Ef|Fl|Cl|"
        r"1\s*Ts|2\s*Ts|1\s*Tm|2\s*Tm|Tt|Fm|Hb|Tg|"
        r"1\s*Pd|2\s*Pd|1\s*Jo|2\s*Jo|3\s*Jo|Jd|Ap"
        r")\s*\d{1,3}\s*,\s*\d{1,3}(?:-\d{1,3}[a-z]?)?",
        re.IGNORECASE,
    )

    resultado = padrao.search(texto)

    if not resultado:
        return None

    return resultado.group(0).strip()


def separar_meditacao(texto: str) -> str:
    padroes_inicio = [
        r"\bMeditação\b",
        r"\bMeditando a palavra\b",
    ]

    inicio = None

    for padrao in padroes_inicio:
        resultado = re.search(
            padrao,
            texto,
            flags=re.IGNORECASE,
        )

        if resultado:
            inicio = resultado.end()
            break

    if inicio is None:
        return texto

    trecho = texto[inicio:]

    marcadores_finais = [
        r"\bRezando a palavra\b",
        r"\bVivendo a palavra\b",
        r"\bComunicando\b",
    ]

    fim = len(trecho)

    for marcador in marcadores_finais:
        resultado = re.search(
            marcador,
            trecho,
            flags=re.IGNORECASE,
        )

        if resultado:
            fim = min(fim, resultado.start())

    texto_principal = normalizar_espacos(trecho[:fim])

    texto_principal = re.sub(
        r"^[\s.:;-]+",
        "",
        texto_principal,
    )

    return texto_principal.strip()


def converter_data_blogger(data_iso: str | None) -> str | None:
    if not data_iso:
        return None

    try:
        data = datetime.fromisoformat(
            data_iso.replace("Z", "+00:00")
        )

        if data.tzinfo is not None:
            data = data.astimezone(FUSO_BRASIL)

        return data.date().isoformat()

    except ValueError:
        return data_iso
    
    
def requisitar_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    resposta = session.get(
        url,
        params=params,
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()
    return resposta.json()


def extrair_posts_feed(
    limite: int = MAX_POSTS,
) -> list[dict[str, Any]]:
    session = criar_sessao()

    payload = requisitar_json(
        session,
        FEED_URL,
        {
            "alt": "json",
            "max-results": limite,
            "orderby": "published",
        },
    )

    entradas = payload.get("feed", {}).get("entry", [])
    posts: list[dict[str, Any]] = []

    for entry in entradas:
        url = localizar_url_post(entry)

        if not url:
            continue

        titulo = normalizar_espacos(
            entry.get("title", {}).get("$t", "")
        )

        conteudo_html = (
            entry.get("content", {}).get("$t")
            or entry.get("summary", {}).get("$t")
            or ""
        )

        texto_completo = html_para_texto(conteudo_html)
        texto_principal = separar_meditacao(texto_completo)

        categorias = [
            normalizar_espacos(categoria.get("term", ""))
            for categoria in entry.get("category", [])
            if categoria.get("term")
        ]

        posts.append(
            {
                "id": criar_id_post(url),
                "fonte": "padre_joao_carlos",
                "titulo": titulo,
                "url": url,
                "data_publicacao": converter_data_blogger(
                    entry.get("published", {}).get("$t")
                ),
                "data_atualizacao": converter_data_blogger(
                    entry.get("updated", {}).get("$t")
                ),
                "categorias": categorias,
                "referencia_biblica": extrair_referencia_biblica(
                    texto_completo
                ),
                "texto_completo": texto_completo,
                "texto_principal": texto_principal,
                "extraido_em": datetime.now()
                .astimezone()
                .isoformat(),
                "processado": False,
            }
        )

    return posts


def extrair_posts_html(
    limite: int = MAX_POSTS,
) -> list[dict[str, Any]]:
    session = criar_sessao()

    resposta = session.get(
        BLOG_URL,
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()

    soup = BeautifulSoup(resposta.text, "html.parser")

    links: list[str] = []

    seletores = [
        "h3.post-title a",
        ".post-title a",
        "article h2 a",
        "article h3 a",
    ]

    for seletor in seletores:
        for elemento in soup.select(seletor):
            href = elemento.get("href")

            if not isinstance(href, str) or not href.strip():
                continue

            url = urljoin(BLOG_URL, href)

            if url not in links:
                links.append(url)

            if len(links) >= limite:
                break

        if links:
            break

    logging.info(
        "%d link(s) encontrado(s) pela página HTML.",
        len(links),
    )

    posts: list[dict[str, Any]] = []

    for url in links[:limite]:
        try:
            post = extrair_post_individual(
                session,
                url,
            )

            if post:
                posts.append(post)

        except requests.RequestException as erro:
            logging.warning(
                "Falha ao extrair %s: %s",
                url,
                erro,
            )

    return posts


def extrair_post_individual(
    session: requests.Session,
    url: str,
) -> dict[str, Any] | None:
    resposta = session.get(
        url,
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()

    soup = BeautifulSoup(resposta.text, "html.parser")

    titulo_elemento = (
        soup.select_one("h3.post-title")
        or soup.select_one("h1.post-title")
        or soup.select_one("article h1")
        or soup.select_one("article h2")
    )

    corpo_elemento = (
        soup.select_one(".post-body")
        or soup.select_one(".entry-content")
        or soup.select_one("article")
    )

    if not titulo_elemento or not corpo_elemento:
        return None

    titulo = normalizar_espacos(
        titulo_elemento.get_text(" ")
    )

    texto_completo = html_para_texto(
        str(corpo_elemento)
    )

    categorias = [
        normalizar_espacos(elemento.get_text(" "))
        for elemento in soup.select("a[rel='tag']")
    ]

    data_elemento = (
        soup.select_one("time[datetime]")
        or soup.select_one(".date-header")
        or soup.select_one(".post-timestamp")
    )

    data_publicacao = None

    if data_elemento:
        data_publicacao = (
            data_elemento.get("datetime")
            or normalizar_espacos(
                data_elemento.get_text(" ")
            )
        )

    return {
        "id": criar_id_post(url),
        "fonte": "padre_joao_carlos",
        "titulo": titulo,
        "url": url,
        "data_publicacao": data_publicacao,
        "data_atualizacao": None,
        "categorias": categorias,
        "referencia_biblica": extrair_referencia_biblica(
            texto_completo
        ),
        "texto_completo": texto_completo,
        "texto_principal": separar_meditacao(
            texto_completo
        ),
        "extraido_em": datetime.now()
        .astimezone()
        .isoformat(),
        "processado": False,
    }


def mesclar_posts(
    existentes: list[dict[str, Any]],
    novos: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    por_id = {
        post["id"]: post
        for post in existentes
        if post.get("id")
    }

    realmente_novos: list[dict[str, Any]] = []

    for post in novos:
        post_id = post["id"]

        if post_id not in por_id:
            por_id[post_id] = post
            realmente_novos.append(post)
            continue

        processado = por_id[post_id].get(
            "processado",
            False,
        )

        por_id[post_id].update(post)
        por_id[post_id]["processado"] = processado

    resultado = list(por_id.values())

    resultado.sort(
        key=lambda item: item.get("data_publicacao") or "",
        reverse=True,
    )

    return resultado, realmente_novos


def executar() -> None:
    PASTA_DADOS.mkdir(
        parents=True,
        exist_ok=True,
    )

    existentes = carregar_json(
        ARQUIVO_POSTS,
        [],
    )

    logging.info(
        "Buscando publicações no feed do Blogger..."
    )

    try:
        extraidos = extrair_posts_feed()

    except (
        requests.RequestException,
        ValueError,
        KeyError,
    ) as erro:
        logging.warning(
            "O feed falhou: %s. Tentando HTML...",
            erro,
        )
        extraidos = extrair_posts_html()

    todos, novos = mesclar_posts(
        existentes,
        extraidos,
    )

    salvar_json(
        ARQUIVO_POSTS,
        todos,
    )

    logging.info(
        "%d publicação(ões) encontrada(s).",
        len(extraidos),
    )
    logging.info(
        "%d publicação(ões) nova(s).",
        len(novos),
    )

    for post in novos:
        logging.info(
            "Novo conteúdo: %s | %s",
            post["titulo"],
            post["url"],
        )


if __name__ == "__main__":
    executar()