# -*- coding: utf-8 -*-
"""
config.py - configuração central, layouts dos arquivos da Receita e funções auxiliares.

Tudo que pode mudar (URLs, token de compartilhamento, ordem das colunas) fica
concentrado aqui para facilitar o ajuste quando a Receita/SPA alterarem algo.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

# --------------------------------------------------------------------------- #
# Pastas de trabalho
# --------------------------------------------------------------------------- #
BASE_DIR = Path(os.environ.get("BETS_DATA_DIR", Path(__file__).resolve().parent / "dados"))
DIR_SPA = BASE_DIR / "spa"              # snapshots brutos da lista da SPA/MF
DIR_RFB_ZIP = BASE_DIR / "rfb_zip"      # zips da Receita, uma subpasta por competência (AAAA-MM)
DIR_PROC = BASE_DIR / "processado"      # tabelas tratadas + banco SQLite
DB_PATH = DIR_PROC / "bets_rede.sqlite"

# --------------------------------------------------------------------------- #
# Fontes
# --------------------------------------------------------------------------- #
# Página da SPA/MF com a lista de empresas autorizadas (vista em 20/09/2026).
SPA_PAGINA_URL = (
    "https://www.gov.br/fazenda/pt-br/composicao/orgaos/secretaria-de-premios-e-apostas/"
    "transparencia-ativa-processos-de-autorizacao-de-apostas-de-quota-fixa/empresas-autorizadas"
)
# Link do CSV que a página exibia em 20/09/2026. Pode mudar: o script 01 tenta
# primeiro descobrir o link atual raspando a página acima.
SPA_CSV_URL_PADRAO = (
    "https://www.gov.br/fazenda/pt-br/composicao/orgaos/secretaria-de-premios-e-apostas/"
    "transparencia-ativa-processos-de-autorizacao-de-apostas-de-quota-fixa/planilha-de-autorizacoes-1.csv"
)

# Dados abertos do CNPJ (Receita Federal). Desde jan/2026 os arquivos ficam num
# compartilhamento Nextcloud acessado via WebDAV. O token abaixo foi copiado do
# script dados_cnpj_baixa.py do projeto rictom/cnpj-sqlite (versão 0.7, mar/2026),
# que por sua vez adaptou a rotina de caiopizzol/cnpj-data-pipeline.
# NÃO é um valor oficial garantido: se a Receita trocar o token, pegue o novo na URL
# da página https://arquivos.receitafederal.gov.br/ (trecho após /index.php/s/) e
# passe por --token ou pela variável de ambiente RFB_SHARE_TOKEN.
RFB_SHARE_TOKEN = os.environ.get("RFB_SHARE_TOKEN", "YggdBLfdninEJX9")
RFB_BASE_WEBDAV = os.environ.get(
    "RFB_BASE_WEBDAV", "https://arquivos.receitafederal.gov.br/public.php/webdav"
)
RFB_BASE_ARQUIVOS = os.environ.get(
    "RFB_BASE_ARQUIVOS", "https://arquivos.receitafederal.gov.br/public.php/dav/files"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36 pesquisa-rede-bets/0.1"
)

# --------------------------------------------------------------------------- #
# Layout dos CSVs da Receita (sem cabeçalho, separador ';').
# Ordem conforme o dicionário oficial https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf
# A Receita mudou o layout de publicação em jan/2026: os scripts VALIDAM o número
# de colunas e abortam com mensagem clara se ele não bater com o que está aqui.
# --------------------------------------------------------------------------- #
COLUNAS = {
    "empresas": [
        "cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
        "capital_social", "porte_empresa", "ente_federativo_responsavel",
    ],
    "estabelecimentos": [
        "cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz_filial", "nome_fantasia",
        "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
        "nome_cidade_exterior", "pais", "data_inicio_atividade", "cnae_fiscal_principal",
        "cnae_fiscal_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
        "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
        "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial",
    ],
    "socios": [
        "cnpj_basico", "identificador_socio", "nome_socio", "cnpj_cpf_socio",
        "qualificacao_socio", "data_entrada_sociedade", "pais", "representante_legal",
        "nome_representante", "qualificacao_representante_legal", "faixa_etaria",
    ],
}

# Tabelas de domínio (2 colunas: código;descrição)
DOMINIOS = {
    "cnaes": "dom_cnaes",
    "motivos": "dom_motivos",
    "municipios": "dom_municipios",
    "naturezas": "dom_naturezas",
    "paises": "dom_paises",
    "qualificacoes": "dom_qualificacoes",
}

# Trechos (sem acento, minúsculos) usados para reconhecer os arquivos pelo nome.
PADROES_ARQUIVO = {
    "empresas": ["empresa"],
    "estabelecimentos": ["estabelec"],
    "socios": ["socio"],
    "simples": ["simples"],
    "cnaes": ["cnae"],
    "motivos": ["motivo"],
    "municipios": ["municipio"],
    "naturezas": ["natureza"],
    "paises": ["pais"],
    "qualificacoes": ["qualifica"],
}

# Naturezas jurídicas de S.A. (código da Receita sem hífen: 204-6 -> 2046).
# Nas S.A. o QSA lista administradores/conselheiros/diretores, NÃO os acionistas
# (Anexo VI da norma da Receita sobre QSA). Por isso o flag é usado no diagnóstico.
NATUREZAS_SA = {"2046": "Sociedade Anônima Aberta", "2054": "Sociedade Anônima Fechada"}

# Colunas de estabelecimentos mantidas por padrão (contato só com --incluir-contato)
EST_COLUNAS_BASE = [
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz_filial", "nome_fantasia",
    "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
    "data_inicio_atividade", "cnae_fiscal_principal", "tipo_logradouro", "logradouro",
    "numero", "complemento", "bairro", "cep", "uf", "municipio",
]
EST_COLUNAS_CONTATO = ["ddd_1", "telefone_1", "ddd_2", "telefone_2", "correio_eletronico"]


# --------------------------------------------------------------------------- #
# Funções auxiliares
# --------------------------------------------------------------------------- #
def classificar_arquivo(nome: str) -> str | None:
    """Tipo do arquivo da Receita (empresas, socios, ...) a partir de trechos do nome."""
    n = normaliza_texto(nome)
    for tipo, trechos in PADROES_ARQUIVO.items():
        if any(t in n for t in trechos):
            return tipo
    return None


def garantir_pastas() -> None:
    for p in (DIR_SPA, DIR_RFB_ZIP, DIR_PROC):
        p.mkdir(parents=True, exist_ok=True)


def sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")


def normaliza_texto(texto: str) -> str:
    """Minúsculas, sem acento, espaços colapsados (para casar cabeçalhos/nomes de arquivo)."""
    return re.sub(r"\s+", " ", sem_acento(texto).lower()).strip()


def normaliza_nome(texto: str) -> str:
    """MAIÚSCULAS, sem acento, espaços colapsados (chave de nome de pessoa/empresa)."""
    return re.sub(r"\s+", " ", sem_acento(texto).upper()).strip()


def normaliza_cnpj(texto: str) -> str | None:
    """
    Devolve o CNPJ com 14 caracteres (sem pontuação) ou None se não parecer um CNPJ.
    Aceita letras nas 12 primeiras posições porque o CNPJ alfanumérico passou a existir
    em 2026 (não tenho uma fonte verificada aqui sobre a data exata: confirme na norma da
    Receita se isso for relevante para a sua análise).
    """
    s = re.sub(r"[^0-9A-Za-z]", "", texto or "").upper()
    if re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", s):
        return s
    return None


def cnpj_basico(cnpj14: str) -> str:
    return cnpj14[:8]


def cnpj_formatado(cnpj14: str) -> str:
    c = cnpj14
    return f"{c[0:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}"


def dv_cnpj_ok(cnpj14: str) -> bool | None:
    """
    Confere os dígitos verificadores de um CNPJ NUMÉRICO (módulo 11).
    Retorna None quando o CNPJ tem letras (não valido esse caso, para não arriscar
    um algoritmo do qual não tenho fonte verificada).
    """
    if not cnpj14 or not cnpj14.isdigit() or len(cnpj14) != 14:
        return None
    nums = [int(c) for c in cnpj14]

    def dv(base: list[int], pesos: list[int]) -> int:
        r = sum(n * p for n, p in zip(base, pesos)) % 11
        return 0 if r < 2 else 11 - r

    d1 = dv(nums[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(nums[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return nums[12] == d1 and nums[13] == d2


def ultima_competencia_local() -> Path | None:
    """Subpasta AAAA-MM mais recente dentro de DIR_RFB_ZIP (ou None)."""
    if not DIR_RFB_ZIP.exists():
        return None
    pastas = sorted(p for p in DIR_RFB_ZIP.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name))
    return pastas[-1] if pastas else None