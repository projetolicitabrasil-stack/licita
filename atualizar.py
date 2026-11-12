#!/usr/bin/env python3
"""Coleta editais ABERTOS (licitações e dispensas) no PNCP e gera um site em docs/.

Uso:
  python3 atualizar.py --demo            -> site de teste com dados FICTÍCIOS na pasta demo/ (não mexe no docs/)
  python3 atualizar.py --teste --uf BA   -> só testa a conexão (não gera site)
  python3 atualizar.py --uf BA           -> coleta só a Bahia (rápido)
  python3 atualizar.py                   -> coleta o BRASIL (27 UFs)
  python3 atualizar.py --regiao nordeste -> coleta só uma região (norte, nordeste, centro-oeste, sudeste, sul)
  python3 atualizar.py --brasil          -> equivalente ao padrão (compatibilidade)
  python3 atualizar.py --site            -> só refaz a página (docs/index.html), sem coletar
Só usa o que já vem no Python; não precisa instalar nada.
"""
import argparse, gzip, json, os, time, unicodedata, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone

BASE = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
MODALIDADES = {1: "Leilão Eletrônico", 2: "Diálogo Competitivo", 3: "Concurso",
               4: "Concorrência Eletrônica", 5: "Concorrência Presencial",
               6: "Pregão Eletrônico", 7: "Pregão Presencial", 8: "Dispensa",
               12: "Credenciamento", 13: "Leilão Presencial"}
UFS = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
NORDESTE = "MA PI CE RN PB PE AL SE BA".split()
REGIOES = {"norte": "AC AP AM PA RO RR TO".split(), "nordeste": NORDESTE,
           "centro-oeste": "DF GO MT MS".split(), "sudeste": "ES MG RJ SP".split(),
           "sul": "PR RS SC".split()}
TOLERANCIA = float(os.environ.get("TOLERANCIA", "0.02"))  # fração de (modalidade x estado) que pode falhar
PAUSA = float(os.environ.get("PAUSA", "0.5"))  # segundos entre consultas, para o PNCP não bloquear


def agora_br():
    """Hora de Brasília (UTC-3, sem horário de verão). O GitHub Actions roda em UTC; os prazos do PNCP são de Brasília."""
    return datetime.now(timezone(timedelta(hours=-3))).replace(tzinfo=None)


def buscar(params):
    """Consulta o PNCP. Devolve None se falhar (nunca finge que veio vazio)."""
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "licitacoes-simples/1.0",
                                               "Accept": "application/json"})
    for t in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                corpo = r.read().decode("utf-8")
            return json.loads(corpo) if corpo.strip() else {"data": [], "totalPaginas": 0}
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                print(f"   erro {e.code}: {e.read()[:200]}")
                return None
            espera = 60 * (t + 1) if e.code == 429 else 10
            print(f"   aviso: HTTP {e.code}; aguardando {espera}s")
            time.sleep(espera)
        except Exception as e:
            print(f"   aviso: {e}; aguardando 10s")
            time.sleep(10)
    return None


def resumir(c):
    org, uni = c.get("orgaoEntidade") or {}, c.get("unidadeOrgao") or {}
    cnpj, ano, seq = org.get("cnpj"), c.get("anoCompra"), c.get("sequencialCompra")
    return {
        "id": c.get("numeroControlePNCP"),
        "uf": uni.get("ufSigla"),
        "mun": uni.get("municipioNome"),
        "mod": c.get("modalidadeNome"),
        "org": org.get("razaoSocial"),
        "obj": (c.get("objetoCompra") or "").strip(),
        "val": c.get("valorTotalEstimado"),
        "fim": c.get("dataEncerramentoProposta"),
        "pub": c.get("dataPublicacaoPncp"),
        "uasg": str((c.get("unidadeOrgao") or {}).get("codigoUnidade") or ""),
        "esfera": ({"F": "Federal", "E": "Estadual", "M": "Municipal", "1": "Federal", "2": "Estadual", "3": "Municipal"}.get(
            str(org.get("esferaId") or org.get("esfera") or "").upper(),
            {"Federal": "Federal", "Estadual": "Estadual", "Municipal": "Municipal"}.get(
                str(org.get("esferaId") or org.get("esfera") or ""), "Não identificada"))),
        "link": f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}" if cnpj and ano and seq else "",
    }


def teste(ufs):
    ate = (agora_br() + timedelta(days=365)).strftime("%Y%m%d")
    for cod in (6, 8):
        print(f"\n=== Modalidade {cod} ({MODALIDADES[cod]}), UF {ufs[0]} ===")
        r = buscar({"dataFinal": ate, "codigoModalidadeContratacao": cod, "uf": ufs[0],
                    "pagina": 1, "tamanhoPagina": 10})
        if r is None:
            print("FALHOU: o PNCP não respondeu.")
            continue
        itens = r.get("data") or []
        print(f"itens na página: {len(itens)} | total de páginas: {r.get('totalPaginas')}")
        if itens:
            print("campos recebidos:", ", ".join(itens[0].keys()))
            print("exemplo resumido:", json.dumps(resumir(itens[0]), ensure_ascii=False)[:600])
        time.sleep(PAUSA)


def coletar_par(cod, uf, achados, agora, ate, inicio=1):
    """Coleta uma modalidade em um estado. Devolve a página em que falhou, ou None se deu certo."""
    nome = MODALIDADES[cod]
    pagina = inicio
    while True:
        r = buscar({"dataFinal": ate, "codigoModalidadeContratacao": cod, "uf": uf,
                    "pagina": pagina, "tamanhoPagina": 50})
        time.sleep(PAUSA)
        if r is None:
            return pagina
        for c in r.get("data") or []:
            s = resumir(c)
            if s["id"] and not (s["fim"] and s["fim"] < agora):
                achados[s["id"]] = s
        total = r.get("totalPaginas") or 0
        print(f"{nome} {uf}: página {pagina}/{total}")
        if pagina >= total:
            return None
        pagina += 1


def coletar(ufs):
    """Devolve (editais, pares que falharam mesmo após 2ª tentativa, total de pares modalidade x estado)."""
    ate = (agora_br() + timedelta(days=365)).strftime("%Y%m%d")
    agora = agora_br().strftime("%Y-%m-%dT%H:%M:%S")
    achados, pendentes = {}, []
    for cod in MODALIDADES:
        for uf in ufs:
            falhou_em = coletar_par(cod, uf, achados, agora, ate)
            if falhou_em:
                pendentes.append((cod, uf, falhou_em))
    falhas = 0
    if pendentes:
        print(f"\n{len(pendentes)} consulta(s) falharam; nova tentativa em 60s...")
        time.sleep(60)
        for cod, uf, pagina in pendentes:
            if coletar_par(cod, uf, achados, agora, ate, pagina):
                falhas += 1
                print(f"   AVISO: {MODALIDADES[cod]} {uf} continua falhando")
    return achados, falhas, len(MODALIDADES) * len(ufs)


def norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


def municipios_ibge(uf):
    """Lista oficial de municípios do estado, segundo o IBGE."""
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            conteudo = r.read()
            # Algumas respostas chegam compactadas em GZIP, mesmo sem aviso no cabeçalho.
            if conteudo.startswith(b"\x1f\x8b"):
                conteudo = gzip.decompress(conteudo)
            municipios = json.loads(conteudo.decode("utf-8"))
            if not isinstance(municipios, list):
                raise ValueError("resposta do IBGE não contém uma lista de municípios")
            return [m["nome"] for m in municipios]
    except Exception as e:
        print(f"   aviso: sem a lista oficial do IBGE para {uf}: {e}")
        return []


def completar(info, uf):
    """Garante que todo município oficial apareça na lista, mesmo com 0 editais."""
    conhecidos = {norm(n) for n in info["mun"]}
    for nome in municipios_ibge(uf):
        if norm(nome) not in conhecidos:
            info["mun"][nome] = 0


def pagina():
    """Devolve o HTML do site, com o desenho do mapa embutido."""
    return PAGINA.replace("__MAPA__", MAPA_JSON)


def gravar(achados, ufs, saida="docs", completa=True, rotulo=""):
    os.makedirs(f"{saida}/dados", exist_ok=True)
    por_uf = {}
    for s in achados.values():
        por_uf.setdefault(s["uf"] or "??", []).append(s)
    onde = "no Brasil" if set(ufs) == set(UFS) else "nos estados selecionados"
    resumo = {"atualizado": agora_br().strftime("%d/%m/%Y %H:%M") + rotulo, "onde": onde, "ufs": {}}
    for uf in sorted(set(por_uf) | set(ufs)):
        lista = por_uf.get(uf, [])
        lista.sort(key=lambda s: s["fim"] or "9")
        mun = {}
        for s in lista:
            m = s["mun"] or "Sem município"
            mun[m] = mun.get(m, 0) + 1
        resumo["ufs"][uf] = {"total": len(lista), "mun": mun}
        if completa and uf in UFS:
            completar(resumo["ufs"][uf], uf)
        with open(f"{saida}/dados/{uf}.json", "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, separators=(",", ":"))
    with open(f"{saida}/dados/resumo.json", "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False)
    with open(f"{saida}/index.html", "w", encoding="utf-8") as f:
        f.write(pagina())
    open(f"{saida}/.nojekyll", "w").close()


def demo():
    """Site de demonstração com dados FICTÍCIOS, gravado em demo/ (o docs/ não é tocado)."""
    import random
    r = random.Random(7)
    objetos = ["Aquisição de material de escritório", "Contratação de serviços de limpeza e conservação",
               "Registro de preços para merenda escolar", "Manutenção preventiva e corretiva da frota municipal",
               "Aquisição de medicamentos e material hospitalar", "Reforma e ampliação de unidade básica de saúde",
               "Contratação de serviços de internet, redes e suporte de informática",
               "Aquisição de equipamentos de informática e periféricos", "Locação de máquinas pesadas com operador",
               "Serviços de instalação e manutenção elétrica em prédios públicos, incluindo fornecimento de "
               "materiais, mão de obra especializada, projeto, laudos técnicos e treinamento das equipes"]
    orgaos = ["Prefeitura Municipal de", "Câmara Municipal de", "Fundo Municipal de Saúde de",
              "Secretaria Municipal de Educação de"]
    cidades = ["Alto Alegre", "Boa Vista do Sul", "Campo Belo", "Dois Riachos", "Esperança", "Santa Rita",
               "São José", "Nova Esperança", "Rio Claro", "Vila Nova", "Barra Mansa", "Monte Verde"]
    mods = ["Pregão Eletrônico", "Pregão Eletrônico", "Dispensa", "Dispensa", "Concorrência Eletrônica",
            "Pregão Presencial", "Credenciamento"]
    valores = [None, 8500, 23000, 48000, 95000, 180000, 420000, 750000, 2300000, 6800000]
    pesos = {"SP": 220, "BA": 160, "MG": 140, "PE": 90, "RS": 80, "PR": 70, "CE": 55, "GO": 45,
             "AM": 25, "AC": 12, "RR": 6, "AP": 0}
    agora = agora_br()
    achados = {}
    for uf in UFS:
        n = pesos.get(uf, r.choice([10, 18, 30, 42]))
        locais = r.sample(cidades, 7)
        for i in range(n):
            dias = r.choice([0, 1, 2, 3, 5, 6, 9, 15, 30, 60])
            if dias == 0:
                fim = agora + timedelta(minutes=45)
            else:
                fim = (agora + timedelta(days=dias)).replace(hour=r.choice([9, 10, 14, 16]), minute=0, second=0)
            cidade = r.choice(locais)
            achados[f"demo-{uf}-{i}"] = {
                "id": f"demo-{uf}-{i}", "uf": uf, "mun": cidade, "mod": r.choice(mods),
                "org": f"{r.choice(orgaos)} {cidade}", "obj": r.choice(objetos),
                "val": r.choice(valores), "fim": fim.strftime("%Y-%m-%dT%H:%M:%S"),
                "pub": (agora-timedelta(hours=r.choice([1,20,36,60,100]))).strftime("%Y-%m-%dT%H:%M:%S"),
                "uasg": str(r.choice(["160199","160001","", "153000"])),
                "esfera": r.choice(["Federal","Estadual","Municipal","Não identificada"]),
                "link": "https://pncp.gov.br/app/editais/00000000000000/2026/1"}
    gravar(achados, UFS, saida="demo", completa=False, rotulo=" (DEMONSTRAÇÃO: dados fictícios)")


PAGINA = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Editais, licitações e dispensas abertas em todo o Brasil, por estado e município.">
<title>Licitações abertas no Brasil</title>
<style>
:root{
  --fundo:#f3f6f9;--painel:#fff;--tinta:#14222e;--suave:#52657a;--linha:#d3dce5;
  --acento:#0b5d8f;--sobre-acento:#fff;--sel:#f2b705;--sel-tinta:#14222e;
  --m0:#e3e9ef;--m1:#cde0ee;--m2:#9dc3de;--m3:#5e9bc5;--m4:#2d76a8;--m5:#0b4f7c;--rot-forte:#fff;
  --u1:#b42318;--u2:#b85100;--u3:#8a6d00}
@media (prefers-color-scheme:dark){:root{
  --fundo:#0f171e;--painel:#16212a;--tinta:#e7eef4;--suave:#9aabbb;--linha:#283643;
  --acento:#62b0e0;--sobre-acento:#0f171e;--sel:#e8b923;
  --m0:#1b2630;--m1:#1f3b50;--m2:#245577;--m3:#2f77a8;--m4:#5aa6d8;--m5:#9ccff2;--rot-forte:#0f171e;
  --u1:#ff7b6b;--u2:#ffa24d;--u3:#e0c24d}}
*{box-sizing:border-box}
body{margin:0;font:16px/1.5 system-ui,"Segoe UI",Roboto,Ubuntu,sans-serif;background:var(--fundo);color:var(--tinta)}
body.travado{overflow:hidden}
.app{max-width:1180px;margin:0 auto;padding:22px 16px 56px}
h1{font-size:1.65rem;line-height:1.2;letter-spacing:-.01em;margin:0 0 4px}
.sub{color:var(--suave);margin:0 0 20px}
button,select,input{font:inherit;color:inherit}
button:focus-visible,select:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid var(--acento);outline-offset:2px}
.corpo{display:grid;grid-template-columns:300px minmax(0,1fr);gap:28px;align-items:start}

/* lateral de filtros */
.lateral{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto;background:var(--painel);
  border:1px solid var(--linha);border-radius:8px;padding:16px}
.lateral label{display:block;font-weight:600;font-size:.92rem;margin:14px 0 4px}
.lateral label:first-of-type{margin-top:0}
.lateral select,.lateral input{width:100%;padding:10px;background:var(--fundo);border:1px solid var(--linha);border-radius:6px}
.lateral :disabled{opacity:.55;cursor:not-allowed}
.aviso{margin:0 0 14px;padding:10px 12px;border-left:4px solid var(--sel);background:var(--fundo);font-size:.92rem}
.gaveta-topo,.so-mobile,.barra-mobile{display:none}
.sec{margin-top:18px;width:100%;padding:10px;background:transparent;border:1px solid var(--linha);border-radius:6px;cursor:pointer}
.sec:hover:not(:disabled){border-color:var(--acento)}
.prim{width:100%;padding:12px;background:var(--acento);color:var(--sobre-acento);border:0;border-radius:6px;font-weight:600;cursor:pointer}

/* mapa */
#painel{display:flex;gap:24px;align-items:flex-start;margin-bottom:22px}
#mapa{flex:0 0 auto;width:min(100%,460px);transition:width .25s}
#mapa svg{display:block;width:100%;height:auto}
#painel.estado #mapa{width:140px}
#lado{flex:1;min-width:0}
#painel.estado #intro,#painel.estado #siglas,#painel.estado #dica{display:none}
#painel.brasil #resumo-uf{display:none}
.uf{stroke:var(--painel);stroke-width:1;stroke-linejoin:round;cursor:pointer;transition:fill .15s}
.uf.m0{fill:var(--m0)}.uf.m1{fill:var(--m1)}.uf.m2{fill:var(--m2)}.uf.m3{fill:var(--m3)}.uf.m4{fill:var(--m4)}.uf.m5{fill:var(--m5)}
.uf:hover{stroke:var(--tinta);stroke-width:1.6}
.uf:focus{outline:none}
.uf:focus-visible{stroke:var(--tinta);stroke-width:3}
.uf.sel{fill:var(--sel);stroke:var(--tinta);stroke-width:2}
#painel.estado .uf:not(.sel){opacity:.5}
.sg{font-size:13px;font-weight:600;fill:var(--tinta);text-anchor:middle;dominant-baseline:central;pointer-events:none}
.sg.b4,.sg.b5{fill:var(--rot-forte)}
.sg.sel{fill:var(--sel-tinta)}
#intro{margin:0 0 14px;font-size:1.05rem}
#siglas{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;gap:6px;align-items:baseline;padding:6px 10px;background:var(--painel);
  border:1px solid var(--linha);border-radius:6px;cursor:pointer;font-weight:600}
.chip small{font-weight:400;color:var(--suave)}
.chip:hover{border-color:var(--acento)}
.chip[aria-pressed="true"]{background:var(--sel);border-color:var(--sel);color:var(--sel-tinta)}
.chip[aria-pressed="true"] small{color:var(--sel-tinta)}
#dica{min-height:1.5em;margin:12px 0 0;color:var(--suave);font-size:.95rem}
#nome-uf{font-size:1.4rem;margin:0}
#qtd-uf{margin:2px 0 12px;color:var(--suave)}
#voltar{padding:8px 14px;background:transparent;border:1px solid var(--linha);border-radius:6px;cursor:pointer}
#voltar:hover{border-color:var(--acento)}

.mun-lista{max-height:240px;overflow:auto;border:1px solid var(--linha);border-radius:6px;padding:7px;margin-top:7px}
.mun-lista label{display:flex!important;align-items:center;gap:7px;font-weight:400!important;margin:4px 0!important;cursor:pointer}
.mun-lista input,.linha-check input{width:auto!important}
.linha-check{display:flex!important;align-items:center;gap:8px}
.selecionados{color:var(--suave);font-size:.84rem;margin-top:4px}
[hidden]{display:none!important}
.modo-check{margin:6px 0 12px!important;font-weight:650!important}
.estados-lista{display:flex;flex-direction:column;gap:8px;max-height:240px;overflow-y:auto}
.estados-lista label{display:flex;align-items:center;gap:8px;width:100%;white-space:normal}
#munLista label{display:flex;align-items:center;gap:7px;width:100%}
#munLista label span{flex:1;min-width:0}
.contagem-mun{margin-left:auto;white-space:nowrap;color:#52657a;background:#eef3f8;border-radius:5px;padding:2px 6px;font-variant-numeric:tabular-nums}
.acoes-municipios{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.rotulo{font-weight:650;margin:10px 0 4px}
.prazo-tag{display:inline-block;border-radius:5px;padding:2px 7px;margin-left:6px;font-size:.75rem;font-weight:700}
.prazo-tag.tres{background:#fff1ce;color:#805000}
.prazo-tag.ultimo{background:#ffe1df;color:#a21b1b}
.fav-topo{display:flex;justify-content:flex-end;margin:0 0 12px}
.fav-topo button,.estrela{border:1px solid var(--linha);background:var(--painel);color:var(--tinta);border-radius:6px;padding:9px 12px;cursor:pointer}
.estrela{font-size:1.3rem;line-height:1;padding:4px 9px;margin-left:auto}
.estrela[aria-pressed="true"]{color:#9b6500;background:#fff1ce}
#areaFavoritos[hidden],#areaPesquisa[hidden]{display:none!important}
#areaFavoritos h2{margin-top:0}
.novo{display:inline-block;border-radius:5px;padding:1px 7px;background:#d9f6e3;color:#116137;font-size:.75rem;font-weight:700;margin-left:6px}
/* resultados */
#contagem{margin:0 0 12px;color:var(--suave)}
.edital{background:var(--painel);border:1px solid var(--linha);border-left:6px solid var(--linha);
  border-radius:3px;padding:14px 16px;margin:0 0 12px}
.edital.u1{border-left-color:var(--u1)}.edital.u2{border-left-color:var(--u2)}.edital.u3{border-left-color:var(--u3)}
.topo-ed{display:flex;flex-wrap:wrap;gap:2px 16px;font-size:.92rem;color:var(--suave)}
.prazo{font-weight:700;color:var(--tinta)}
.u1 .prazo{color:var(--u1)}.u2 .prazo{color:var(--u2)}.u3 .prazo{color:var(--u3)}
.obj{font-size:1.04rem;font-weight:600;line-height:1.4;margin:6px 0 2px;display:-webkit-box;
  -webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.obj.aberto{display:block}
.ler{padding:0;margin:0 0 4px;background:none;border:0;color:var(--acento);font-weight:600;cursor:pointer}
.org{margin:4px 0 8px;color:var(--suave)}
.rodape{display:flex;flex-wrap:wrap;gap:4px 18px;align-items:baseline;margin:0;font-size:.95rem}
.valor{font-weight:700}
.rodape a{color:var(--acento);font-weight:600;margin-left:auto}
#mais{display:block;margin:8px auto 0;padding:11px 22px;background:var(--acento);color:var(--sobre-acento);
  border:0;border-radius:6px;font-weight:600;cursor:pointer}
#mais[hidden]{display:none}

/* celular: a lateral vira gaveta */
@media (max-width:860px){
  .app{padding-top:16px}
  .corpo{display:block}
  .barra-mobile{display:flex;justify-content:space-between;align-items:center;gap:12px;position:sticky;top:0;z-index:5;
    margin:0 -16px 16px;padding:10px 16px;background:var(--fundo);border-bottom:1px solid var(--linha)}
  #abrir{padding:9px 16px;background:var(--acento);color:var(--sobre-acento);border:0;border-radius:6px;font-weight:600;cursor:pointer}
  #badge[hidden]{display:none}
  #badge{display:inline-block;min-width:1.4em;margin-left:6px;padding:0 6px;border-radius:99px;background:var(--sobre-acento);color:var(--acento);font-size:.85rem}
  #contagem-m{color:var(--suave);font-size:.92rem;text-align:right}
  .lateral{position:fixed;inset:0;z-index:20;max-height:none;border:0;border-radius:0;padding:16px 16px 28px;
    visibility:hidden;transform:translateY(100%);transition:transform .25s,visibility .25s}
  .lateral.aberta{visibility:visible;transform:none}
  .gaveta-topo{display:flex;justify-content:space-between;align-items:center;margin:0 0 14px;font-size:1.1rem}
  .gaveta-topo button{padding:8px 14px;background:transparent;border:1px solid var(--linha);border-radius:6px;cursor:pointer}
  .so-mobile{display:block;margin-top:12px}
  #painel{flex-direction:column;gap:16px}
  #painel.brasil #mapa{width:100%}
  #painel.estado{flex-direction:row;align-items:center}
  #painel.estado #mapa{width:96px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><div class="app">
<h1>Licitações abertas no Brasil</h1>
<div class="fav-topo"><button type="button" id="abrirFavoritos">★ Meus Favoritos (<span id="totalFavoritos">0</span>)</button></div>
<p class="sub" id="atual">Carregando…</p>
<div class="barra-mobile">
  <button type="button" id="abrir" aria-expanded="false" aria-controls="lateral">Filtros<span id="badge" hidden></span></button>
  <span id="contagem-m"></span>
</div>
<div class="corpo">
<aside class="lateral" id="lateral" aria-label="Filtros">
  <div class="gaveta-topo"><strong>Filtros</strong><button type="button" id="fechar">Fechar</button></div>
  <p class="aviso" id="aviso">Escolha um estado no modo simples ou ative a seleção múltipla.</p>
  <label class="linha-check modo-check"><input id="avancado" type="checkbox"> Seleção múltipla de estados e municípios</label>
  <section id="localSimples" aria-label="Localização simples">
    <label for="uf">Estado</label>
    <select id="uf"></select>
    <label for="munSimples">Município</label>
    <select id="munSimples"><option value="">Todos os municípios</option></select>
  </section>
  <section id="localAvancado" hidden aria-label="Localização avançada">
    <p class="rotulo">Selecione os estados</p>
    <div id="estadosLista" class="mun-lista estados-lista"></div>
    <label for="munBusca">Pesquisar município (opcional)</label>
    <input id="munBusca" type="search" placeholder="Filtrar a lista..." autocomplete="off">
    <div id="munEscolhidos" class="selecionados"></div>
    <div id="munLista" class="mun-lista"></div>
    <div class="acoes-municipios">
      <button type="button" id="todosMun" class="sec">Selecionar todos</button>
      <button type="button" id="limparMun" class="sec">Limpar municípios</button>
    </div>
  </section>
  <label for="uasg">UASG / código da unidade</label>
  <input id="uasg" type="search" placeholder="Ex.: 160199" inputmode="numeric">
  <label class="linha-check"><input id="novos" type="checkbox"> Somente publicações novas (48h)</label>
  <label for="q">Buscar no objeto ou órgão</label>
  <input id="q" type="search" placeholder="Ex.: merenda, medicamentos">
  <label for="mod">Modalidade</label>
  <select id="mod"><option value="">Todas as modalidades</option></select>
  <label class="linha-check"><input id="somenteFavoritos" type="checkbox"> Somente favoritos</label>
  <label for="esfera">Esfera administrativa</label>
  <select id="esfera"><option value="">Todas as esferas</option><option value="Federal">Federal</option><option value="Estadual">Estadual</option><option value="Municipal">Municipal</option><option value="Não identificada">Não identificada</option></select>
  <label for="prazo">Prazo das propostas</label>
  <select id="prazo">
    <option value="">Todos os editais abertos (padrão)</option>
    <option value="5">Próximos 5 dias</option>
    <option value="1">Próximas 24 horas</option>
  </select>
  <label for="valor">Valor estimado</label>
  <select id="valor">
    <option value="">Qualquer valor</option>
    <option value="a">Até R$ 50 mil</option>
    <option value="b">De R$ 50 mil a R$ 500 mil</option>
    <option value="c">De R$ 500 mil a R$ 5 milhões</option>
    <option value="d">Acima de R$ 5 milhões</option>
    <option value="n">Valor não informado</option>
  </select>
  <button type="button" id="limpar" class="sec">Limpar filtros</button>
  <div class="so-mobile"><button type="button" id="ver" class="prim">Ver editais</button></div>
</aside>
<main>
  <section id="areaFavoritos" hidden aria-label="Meus favoritos">
    <h2>★ Meus Favoritos</h2>
    <p>Editais salvos neste navegador, inclusive os encerrados.</p>
    <button type="button" id="voltarPesquisa" class="sec">← Voltar às licitações</button>
    <p id="contagemFavoritos" aria-live="polite"></p>
    <div id="listaFavoritos"></div>
  </section>
  <div id="areaPesquisa">
  <section id="painel" class="brasil" aria-label="Mapa por estado">
    <div id="mapa"></div>
    <div id="lado">
      <p id="intro"></p>
      <div id="resumo-uf"><h2 id="nome-uf"></h2><p id="qtd-uf"></p><button type="button" id="voltar">Ver o Brasil</button></div>
      <nav id="siglas" aria-label="Estados"></nav>
      <p id="dica"></p>
    </div>
  </section>
  <p id="contagem" aria-live="polite"></p>
  <div id="lista"></div>
  <button type="button" id="mais" hidden>Mostrar mais</button>
  </div>
</main>
</div>
<script>
const $=id=>document.getElementById(id);
const NOMES={AC:'Acre',AL:'Alagoas',AP:'Amapá',AM:'Amazonas',BA:'Bahia',CE:'Ceará',DF:'Distrito Federal',ES:'Espírito Santo',GO:'Goiás',MA:'Maranhão',MT:'Mato Grosso',MS:'Mato Grosso do Sul',MG:'Minas Gerais',PA:'Pará',PB:'Paraíba',PR:'Paraná',PE:'Pernambuco',PI:'Piauí',RJ:'Rio de Janeiro',RN:'Rio Grande do Norte',RS:'Rio Grande do Sul',RO:'Rondônia',RR:'Roraima',SC:'Santa Catarina',SP:'São Paulo',SE:'Sergipe',TO:'Tocantins'};
const MAPA=__MAPA__;
const e=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=n=>n.toLocaleString('pt-BR');
const plural=(n,s,p)=>num(n)+' '+(n===1?s:p);
const edit=n=>plural(n,'edital aberto','editais abertos');
const brl=v=>v!=null?v.toLocaleString('pt-BR',{style:'currency',currency:'BRL'}):'Valor não informado';
const dt=s=>{const d=s?new Date(s):null;return d&&!isNaN(d)?d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'}):'sem data'};
const norm=s=>String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
let resumo,dados=[],lista=[],qtd=50,sel='',seq=0;
const CHAVE_FAVORITOS='licitacoes-brasil-favoritos-v33';
let favoritos={};let telaFavoritos=false;
try{const salvo=JSON.parse(localStorage.getItem(CHAVE_FAVORITOS)||'{}');if(salvo&&typeof salvo==='object'&&!Array.isArray(salvo))favoritos=salvo}catch(err){console.warn('Favoritos indisponíveis',err)}
function guardarFavoritos(){try{localStorage.setItem(CHAVE_FAVORITOS,JSON.stringify(favoritos))}catch(err){console.warn('Não foi possível salvar favoritos',err)}}
function semBusca(x){const {_t,...r}=x;return r}
function atualizarTotalFavoritos(){$('totalFavoritos').textContent=Object.keys(favoritos).length}
function atualizarFavoritosComDados(){
  for(const x of dados){const id=String(x.id);if(favoritos[id])favoritos[id]=semBusca(x);}
  guardarFavoritos();
}
function alterarFavorito(id){
  const x=dados.find(v=>String(v.id)===id)||favoritos[id];
  if(favoritos[id])delete favoritos[id];else if(x)favoritos[id]=semBusca(x);
  guardarFavoritos();atualizarTotalFavoritos();
  if(telaFavoritos)desenharFavoritos();else filtrar(true);
}
function abrirTelaFavoritos(sim){
  telaFavoritos=sim;$('areaFavoritos').hidden=!sim;$('areaPesquisa').hidden=sim;
  if(sim)desenharFavoritos();window.scrollTo({top:0,behavior:'instant'});
}
function desenharFavoritos(){
  const arr=Object.values(favoritos).sort((a,b)=>{
    const ta=a.fim?new Date(a.fim).getTime():Infinity,tb=b.fim?new Date(b.fim).getTime():Infinity;
    const aa=ta<Date.now()?1:0,bb=tb<Date.now()?1:0;
    return aa-bb||ta-tb;
  });
  $('contagemFavoritos').textContent=arr.length?plural(arr.length,'edital salvo','editais salvos'):'Nenhum edital salvo. Clique na estrela de um edital para adicioná-lo.';
  $('listaFavoritos').innerHTML=arr.map(cartao).join('');
}
const cidadesMarcadas=new Set(), estadosMarcados=new Set();const cacheUF=new Map();let ufsAtivas=[];

function diasAte(fim){
  const f=fim?new Date(fim):null;if(!f||isNaN(f))return null;
  const h=new Date();h.setHours(0,0,0,0);f.setHours(0,0,0,0);
  return Math.round((f-h)/86400000);
}
function prazoInfo(fim){
  const d=diasAte(fim);
  if(d===null)return{t:'Sem data de encerramento',c:''};
  if(d<0)return{t:'Encerrado',c:''};
  if(d===0)return{t:'Encerra hoje',c:'u1'};
  if(d===1)return{t:'Encerra amanhã',c:'u1'};
  return{t:'Encerra em '+d+' dias',c:d<=3?'u2':d<=7?'u3':''};
}
function faixaValor(v,f){
  if(f==='n')return !v;
  if(!v)return false;
  return f==='a'?v<=50000:f==='b'?v>50000&&v<=500000:f==='c'?v>500000&&v<=5000000:v>5000000;
}

async function init(){
  try{resumo=await (await fetch('dados/resumo.json')).json()}
  catch(x){$('atual').textContent='Ainda não há dados. Rode o atualizar.py primeiro.';return}
  $('atual').textContent='Atualizado em '+resumo.atualizado;
  const ufs=Object.keys(resumo.ufs).filter(u=>NOMES[u]);
  const total=ufs.reduce((s,u)=>s+resumo.ufs[u].total,0);
  $('intro').textContent='Há '+edit(total)+' '+(resumo.onde||'no país')+'. Selecione cidades de diferentes estados nos filtros ou toque no mapa.';
  $('uf').innerHTML='<option value="">Brasil inteiro</option>'+ufs.sort((a,b)=>NOMES[a].localeCompare(NOMES[b],'pt')).map(u=>'<option value="'+u+'">'+e(NOMES[u])+' ('+num(resumo.ufs[u].total)+')</option>').join('');
  $('siglas').innerHTML=ufs.sort().map(u=>'<button type="button" class="chip" data-uf="'+u+'" aria-pressed="false" aria-label="'+e(NOMES[u]+': '+edit(resumo.ufs[u].total))+'">'+u+'<small>'+num(resumo.ufs[u].total)+'</small></button>').join('');
  desenharMapa();liberar(true);preencherEstados();preencherMunSimples();preencherMun();atualizarModo();
}

function desenharMapa(){
  const tot=u=>(resumo.ufs[u]||{}).total||0;
  const max=Math.max(1,...Object.keys(MAPA.ufs).map(tot));
  let p='',t='';
  for(const u in MAPA.ufs){
    const g=MAPA.ufs[u],n=tot(u),b=n?1+Math.floor(Math.sqrt(n/max)*4.99):0;
    const rot=NOMES[u]+': '+edit(n);
    p+='<path class="uf m'+b+'" data-uf="'+u+'" d="'+g.d+'" tabindex="0" role="button" aria-label="'+e(rot)+'"><title>'+e(rot)+'</title></path>';
    if(g.g)t+='<text class="sg b'+b+'" data-uf="'+u+'" x="'+g.x+'" y="'+g.y+'">'+u+'</text>';
  }
  $('mapa').innerHTML='<svg viewBox="0 0 '+MAPA.vb[0]+' '+MAPA.vb[1]+'" role="group" aria-label="Mapa do Brasil. Escolha um estado para ver os editais.">'+p+'<g id="rotulos">'+t+'</g></svg>';
}
function marcarMapa(){
  const svg=$('mapa').querySelector('svg');
  document.querySelectorAll('.chip').forEach(c=>c.setAttribute('aria-pressed',String(c.dataset.uf===sel)));
  if(!svg)return;
  svg.querySelectorAll('.sel').forEach(x=>x.classList.remove('sel'));
  if(!sel)return;
  const p=svg.querySelector('.uf[data-uf="'+sel+'"]'),t=svg.querySelector('.sg[data-uf="'+sel+'"]');
  if(p){
    const foco=document.activeElement===p;
    p.classList.add('sel');svg.insertBefore(p,$('rotulos'));
    if(foco)p.focus({preventScroll:true});
  }
  if(t)t.classList.add('sel');
}
function modo(){
  $('painel').className=sel?'estado':'brasil';
  if(!sel)return;
  const r=resumo.ufs[sel],cid=Object.values(r.mun).filter(v=>v>0).length;
  $('nome-uf').textContent=NOMES[sel];
  $('qtd-uf').textContent=edit(r.total)+(r.total?' em '+plural(cid,'município','municípios'):'');
}

function escolher(u){if(!resumo||!resumo.ufs[u])return;$('avancado').checked=false;$('uf').value=u;preencherMunSimples();atualizarModo()}
async function obterUF(u){
  if(cacheUF.has(u))return cacheUF.get(u);
  const r=await fetch('dados/'+u+'.json');if(!r.ok)throw Error('Falha em '+u);
  const d=(await r.json()).map(x=>({...x,_t:norm((x.obj||'')+' '+(x.org||'')+' '+(x.uasg||''))}));
  cacheUF.set(u,d);return d;
}
function avancado(){return $('avancado').checked}
function preencherEstados(){
  const ufs=Object.keys(resumo.ufs).filter(u=>NOMES[u]).sort((a,b)=>NOMES[a].localeCompare(NOMES[b],'pt'));
  $('estadosLista').innerHTML=ufs.map(u=>'<label><input type="checkbox" data-estado="'+u+'"> '+e(NOMES[u])+' ('+u+')</label>').join('');
}
function qtdMunicipio(u,n){
  return Number(((resumo.ufs[u]||{}).mun||{})[n]||0);
}
function municipiosDoEstado(u){
  return Object.keys((resumo.ufs[u]||{}).mun||{}).filter(n=>n!=='Sem município')
    .sort((a,b)=>a.localeCompare(b,'pt'));
}
function preencherMunSimples(){
  const u=$('uf').value, anterior=$('munSimples').value;
  $('munSimples').innerHTML='<option value="">Todos os municípios</option>'+
    (u?municipiosDoEstado(u).map(n=>'<option value="'+e(n)+'">'+e(n)+' ('+num(qtdMunicipio(u,n))+')</option>').join(''):'');
  $('munSimples').value=u&&municipiosDoEstado(u).includes(anterior)?anterior:'';
  $('munSimples').disabled=!u;
}
function atualizarCidades(){
  if(!resumo)return;
  const termo=norm($('munBusca').value), candidatos=[];
  for(const u of [...estadosMarcados].sort()){
    for(const nome of municipiosDoEstado(u)){
      if(!termo||norm(nome).includes(termo))candidatos.push({id:u+'|'+nome,u,nome});
    }
  }
  candidatos.sort((a,b)=>a.u.localeCompare(b.u)||a.nome.localeCompare(b.nome,'pt'));
  $('munLista').innerHTML=candidatos.map(x=>'<label><input type="checkbox" data-cidade="'+e(x.id)+'" '+(cidadesMarcadas.has(x.id)?'checked':'')+'> <span>'+e(x.nome)+' / '+x.u+'</span><small class="contagem-mun">'+num(qtdMunicipio(x.u,x.nome))+'</small></label>').join('') ||
    '<small>'+(estadosMarcados.size?'Nenhum município encontrado.':'Marque um ou mais estados acima.')+'</small>';
  $('munEscolhidos').textContent=estadosMarcados.size+' estado(s), '+cidadesMarcadas.size+' município(s) marcado(s). '+
    (cidadesMarcadas.size?'':'Sem municípios marcados: serão considerados todos os municípios dos estados escolhidos.');
}
function atualizarModo(){
  $('localSimples').hidden=avancado();
  $('localAvancado').hidden=!avancado();
  carregar();
}
async function carregar(){
  const meu=++seq;sel=$('uf').value;marcarMapa();modo();
  $('contagem').textContent='Carregando editais…';$('lista').innerHTML='';$('mais').hidden=true;
  ufsAtivas=avancado()?[...estadosMarcados]:(sel?[sel]:Object.keys(resumo.ufs));
  if(avancado()&&!ufsAtivas.length){dados=[];lista=[];$('contagem').textContent='Marque pelo menos um estado no modo avançado.';return;}
  try{
    const blocos=await Promise.all(ufsAtivas.map(obterUF));if(meu!==seq)return;
    dados=blocos.flat();atualizarFavoritosComDados();preencherMod();filtrar();
  }catch(err){if(meu!==seq)return;dados=[];lista=[];$('contagem').textContent='Erro ao carregar dados: '+err.message;}
}
function preencherMun(){atualizarCidades()}
function preencherMod(){
  const c={};dados.forEach(x=>{const k=x.mod||'Outra';c[k]=(c[k]||0)+1});
  const valor=$('mod').value;
  $('mod').innerHTML='<option value="">Todas as modalidades</option>'+Object.keys(c).sort((a,b)=>c[b]-c[a]).map(n=>'<option value="'+e(n)+'">'+e(n)+' ('+c[n]+')</option>').join('');
  if(c[valor])$('mod').value=valor;
}
function liberar(on){['q','uf','munSimples','avancado','munBusca','uasg','novos','somenteFavoritos','mod','prazo','valor','esfera','limpar','todosMun','limparMun'].forEach(id=>$(id).disabled=!on);$('aviso').hidden=true}
function instante(fim){const t=fim?new Date(fim).getTime():NaN;return Number.isFinite(t)?t:null}
function aberto(x){const t=instante(x.fim);return t===null||t>Date.now()}
function pertoDoFim(x,horas){const t=instante(x.fim);return t!==null&&t>Date.now()&&t<=Date.now()+horas*3600000}
function recente(pub){if(!pub)return false;const t=new Date(pub).getTime();return Number.isFinite(t)&&t<=Date.now()&&t>=Date.now()-48*3600000}
function filtrar(manter){
  const md=$('mod').value,pz=$('prazo').value,vl=$('valor').value,q=norm($('q').value.trim()),uasg=$('uasg').value.trim(),esfera=$('esfera').value;
  lista=dados.filter(x=>{
    if(!aberto(x))return false;
    if($('somenteFavoritos').checked&&!favoritos[String(x.id)])return false;
    if(esfera&&String(x.esfera||'Não identificada')!==esfera)return false;
    if(avancado()){if(cidadesMarcadas.size&&!cidadesMarcadas.has(x.uf+'|'+x.mun))return false;}
    else if($('munSimples').value&&x.mun!==$('munSimples').value)return false;
    if(md&&(x.mod||'Outra')!==md)return false;
    if(pz!==''&&!pertoDoFim(x,Number(pz)*24+(pz==='0'?24:0)))return false
    if(vl&&!faixaValor(x.val,vl))return false;
    if(uasg&&!String(x.uasg||'').includes(uasg))return false;
    if($('novos').checked&&!recente(x.pub))return false;
    if(q&&!x._t.includes(q))return false;
    return true;
  });qtd=manter===true?qtd:50;desenhar();
}

function cartao(x){
  const p=prazoInfo(x.fim);
  const etiqueta=recente(x.pub)?'<span class="novo">Novo · 48h</span>':'';
  const encerrado=instante(x.fim)!==null&&!aberto(x);
  const prazoTag=encerrado?'<span class="prazo-tag ultimo">Encerrado</span>':pertoDoFim(x,24)?'<span class="prazo-tag ultimo">Últimas 24h</span>':pertoDoFim(x,72)?'<span class="prazo-tag tres">Encerra em breve · 3 dias</span>':'';
  const fav=Boolean(favoritos[x.id]);
  const estrela='<button type="button" class="estrela" data-fav="'+e(x.id)+'" aria-label="'+(fav?'Remover dos favoritos':'Adicionar aos favoritos')+'" aria-pressed="'+fav+'" title="'+(fav?'Remover favorito':'Salvar favorito')+'">'+(fav?'★':'☆')+'</button>';
  const link=(x.link||'').startsWith('https://pncp.gov.br/')?'<a href="'+e(x.link)+'" target="_blank" rel="noopener">Ver edital no PNCP</a>':'';
  return '<article class="edital '+p.c+'"><div class="topo-ed"><span class="prazo">'+e(p.t)+'</span><span>Propostas até '+e(dt(x.fim))+'</span></div>'
    +'<h2 class="obj">'+e(x.obj||'Objeto não informado')+etiqueta+prazoTag+'</h2><button type="button" class="ler" aria-expanded="false" hidden>Ler tudo</button>'
    +'<p class="org">'+e(x.org)+'</p>'
    +'<p class="rodape"><span class="valor">'+e(brl(x.val))+'</span><span>'+e(x.mod)+'</span><span>'+e(x.mun||'Sem município')+'/'+e(x.uf)+'</span>'+(x.uasg?'<span>Unidade/UASG: '+e(x.uasg)+'</span>':'')+'<span>Esfera: '+e(x.esfera||'Não identificada')+'</span>'+link+estrela+'</p></article>';
}
function ativos(){return ['mod','prazo','valor','q','uasg','esfera'].filter(id=>$(id).value.trim()!=='').length+Number($('novos').checked)+Number($('somenteFavoritos').checked)+(avancado()?Number(estadosMarcados.size>0)+Number(cidadesMarcadas.size>0):Number(!!$('munSimples').value))}
function desenhar(){
  const n=lista.length,a=ativos();
  let msg='';
  if(true){
    if(n)msg=edit(n)+(a?' com os filtros escolhidos':'');
    else if(a===1&&((avancado()&&cidadesMarcadas.size)||(!avancado()&&$('munSimples').value)))msg='Nenhum edital aberto no PNCP para este município. Algumas prefeituras publicam só no site próprio ou no diário oficial.';
    else msg='Nenhum edital com esses filtros. Tente limpar alguns deles.';
  }
  if(!(!dados.length&&$('contagem').textContent.startsWith('Erro ao carregar')))$('contagem').textContent=msg;
  $('contagem-m').textContent=n?edit(n):'';
  $('ver').textContent=n?'Ver '+edit(n):'Ver editais';
  $('badge').hidden=!a;$('badge').textContent=a;
  $('lista').innerHTML=lista.slice(0,qtd).map(cartao).join('');
  document.querySelectorAll('#lista .obj').forEach(o=>{if(o.scrollHeight>o.clientHeight+1)o.nextElementSibling.hidden=false});
  $('mais').hidden=lista.length<=qtd;
}

$('uf').onchange=()=>{preencherMunSimples();carregar()};
$('munSimples').onchange=filtrar;
$('avancado').onchange=atualizarModo;
$('estadosLista').onchange=ev=>{
  const u=ev.target.dataset.estado;if(!u)return;
  if(ev.target.checked)estadosMarcados.add(u);
  else {estadosMarcados.delete(u);for(const id of [...cidadesMarcadas])if(id.startsWith(u+'|'))cidadesMarcadas.delete(id);}
  atualizarCidades();carregar();
};
['mod','prazo','valor','esfera','novos','somenteFavoritos'].forEach(id=>$(id).onchange=filtrar);
$('uasg').oninput=filtrar;
$('munBusca').oninput=atualizarCidades;
$('munLista').onchange=ev=>{const el=ev.target;if(!el.dataset.cidade)return;el.checked?cidadesMarcadas.add(el.dataset.cidade):cidadesMarcadas.delete(el.dataset.cidade);atualizarCidades();filtrar()};
$('todosMun').onclick=()=>{for(const u of estadosMarcados)for(const n of municipiosDoEstado(u))cidadesMarcadas.add(u+'|'+n);atualizarCidades();filtrar()};
$('limparMun').onclick=()=>{cidadesMarcadas.clear();atualizarCidades();filtrar()};
$('q').oninput=filtrar;
$('limpar').onclick=()=>{['mod','prazo','valor','esfera','q','uasg','munBusca'].forEach(id=>$(id).value='');$('novos').checked=false;$('somenteFavoritos').checked=false;cidadesMarcadas.clear();estadosMarcados.clear();$('estadosLista').querySelectorAll('input').forEach(x=>x.checked=false);$('munSimples').value='';atualizarCidades();carregar()};
$('voltar').onclick=()=>{$('uf').value='';preencherMunSimples();carregar()};
$('mais').onclick=()=>{qtd+=50;desenhar()};
$('mapa').addEventListener('click',ev=>{const p=ev.target.closest('.uf');if(p)escolher(p.dataset.uf)});
$('mapa').addEventListener('keydown',ev=>{
  const p=ev.target.closest&&ev.target.closest('.uf');
  if(p&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();escolher(p.dataset.uf)}
});
$('siglas').addEventListener('click',ev=>{const b=ev.target.closest('.chip');if(b)escolher(b.dataset.uf)});
function dica(ev){
  const el=ev.target.closest&&ev.target.closest('[data-uf]');
  const u=el&&el.dataset.uf;
  $('dica').textContent=u&&resumo.ufs[u]?NOMES[u]+': '+edit(resumo.ufs[u].total):'';
}
['mouseover','focusin'].forEach(t=>{$('mapa').addEventListener(t,dica);$('siglas').addEventListener(t,dica)});
['mouseleave','focusout'].forEach(t=>{$('mapa').addEventListener(t,()=>$('dica').textContent='');$('siglas').addEventListener(t,()=>$('dica').textContent='')});
$('abrirFavoritos').onclick=()=>abrirTelaFavoritos(true);
$('voltarPesquisa').onclick=()=>abrirTelaFavoritos(false);
function cliqueCartao(ev){
  const star=ev.target.closest('.estrela');
  if(star){alterarFavorito(star.dataset.fav);return}
  const b=ev.target.closest('.ler');if(!b)return;
  const o=b.previousElementSibling,aberto=o.classList.toggle('aberto');
  b.textContent=aberto?'Mostrar menos':'Ler tudo';b.setAttribute('aria-expanded',String(aberto));
}
$('listaFavoritos').addEventListener('click',cliqueCartao);
$('lista').addEventListener('click',cliqueCartao);
function gaveta(abrir){
  $('lateral').classList.toggle('aberta',abrir);
  $('abrir').setAttribute('aria-expanded',String(abrir));
  document.body.classList.toggle('travado',abrir);
  (abrir?$('uf'):$('abrir')).focus();
}
$('abrir').onclick=()=>gaveta(true);
$('fechar').onclick=$('ver').onclick=()=>gaveta(false);
document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&$('lateral').classList.contains('aberta'))gaveta(false)});
atualizarTotalFavoritos();init();
</script></div></body></html>
"""

MAPA_JSON = r'''{"vb":[600, 618.4],"ufs":{
"AC":{"d":"M110.0 239.1L112.8 240.3L112.7 241.0L109.0 243.3L106.8 246.1L100.8 247.9L100.2 249.2L96.7 251.6L96.2 253.3L93.8 252.2L91.2 252.4L88.1 257.2L83.4 259.6L80.7 260.2L80.1 257.9L77.7 258.2L69.9 256.7L65.1 257.5L62.1 256.6L56.4 259.0L54.4 258.5L53.0 256.8L51.6 257.9L51.6 239.2L52.9 238.3L51.9 235.1L52.6 235.2L53.5 232.9L52.0 233.2L45.9 239.1L43.3 240.0L42.5 241.5L27.4 242.0L28.2 238.8L26.4 238.1L26.1 234.7L19.0 232.7L11.9 232.7L15.2 229.3L16.1 226.0L14.5 224.6L12.9 221.2L10.8 220.1L10.5 217.8L9.2 217.8L6.9 215.8L5.6 210.8L3.3 208.8L3.5 208.1L4.5 208.3L4.7 206.8L0.1 203.5L1.1 201.9L0.4 200.1L1.8 200.5L4.4 199.3L2.9 196.2L21.5 203.2L60.3 207.9L81.8 226.9L99.1 235.1Z","x":53.9,"y":229.6,"g":1},
"AL":{"d":"M594.5 224.8L591.4 230.2L577.4 246.4L575.6 249.9L574.5 248.5L572.9 248.7L572.0 246.1L568.7 245.2L566.3 241.6L562.6 240.4L559.8 238.3L557.3 237.8L555.3 236.2L554.2 236.3L550.6 233.6L547.8 232.8L547.3 231.4L549.5 228.9L551.2 228.5L554.6 223.9L555.5 226.0L558.7 225.6L559.0 226.6L561.4 227.7L562.6 230.0L563.2 229.6L563.7 230.5L564.6 230.0L567.0 232.2L568.2 230.4L570.9 230.5L572.3 231.6L574.8 229.5L578.1 228.9L577.5 227.8L579.7 226.6L579.6 225.5L583.1 223.9L584.7 223.8L585.4 224.9L589.6 223.2Z","x":572.0,"y":234.3,"g":1},
"AM":{"d":"M102.0 51.4L102.8 53.8L104.6 54.2L105.5 56.1L105.7 65.0L109.2 64.0L117.5 71.9L121.0 71.5L121.3 70.8L122.9 70.7L124.2 68.8L128.7 67.6L130.1 69.6L128.6 72.1L129.4 73.3L131.1 72.3L132.6 68.8L134.8 68.9L135.3 65.7L136.1 65.2L137.3 65.9L138.2 64.0L139.6 64.0L140.5 62.7L141.5 64.1L146.9 59.3L147.6 59.7L146.8 61.4L147.8 61.9L148.3 60.3L151.5 57.9L152.0 52.9L153.0 52.2L158.0 51.6L158.8 50.2L161.8 49.8L162.6 48.5L163.9 49.4L166.1 49.1L167.4 51.4L170.7 51.6L172.8 52.9L172.5 56.4L171.2 58.3L173.8 60.8L176.8 68.2L176.5 71.1L175.4 72.6L175.4 75.5L176.7 77.5L177.1 82.1L179.8 86.3L179.8 88.3L180.7 88.8L178.8 91.7L179.0 93.9L177.3 95.1L176.1 94.3L175.7 95.6L183.3 101.6L185.1 105.7L189.1 106.3L191.6 108.6L190.6 106.3L189.4 105.6L190.5 100.4L189.9 98.4L191.7 94.1L195.4 92.4L195.5 91.5L200.1 92.3L202.5 95.6L202.6 97.2L203.9 97.7L206.0 97.4L206.8 95.8L209.4 95.0L208.0 91.6L213.6 79.4L231.1 79.4L231.4 89.0L233.6 90.4L234.0 94.3L238.1 97.5L238.2 99.8L239.8 101.7L240.9 101.5L242.3 103.0L244.5 101.1L245.4 105.8L249.2 107.4L249.7 108.8L250.9 108.4L252.0 109.7L253.9 110.1L254.1 110.8L257.6 110.8L259.5 113.8L262.4 115.6L264.2 115.6L263.6 117.9L265.0 118.6L267.3 117.4L269.1 118.0L273.9 115.7L272.0 118.3L269.5 119.5L268.3 121.9L269.2 122.5L240.9 185.8L237.5 189.7L238.1 193.0L241.6 196.6L242.7 200.1L241.5 201.7L241.7 204.3L238.9 207.8L240.4 211.7L240.0 215.4L238.4 218.1L238.8 219.7L238.1 221.5L239.8 221.7L238.8 222.6L189.9 223.0L187.9 221.2L187.0 222.2L186.1 221.9L185.7 223.8L183.8 224.2L183.1 223.0L181.6 223.0L180.7 219.7L179.4 219.4L179.2 220.4L178.4 220.0L178.0 216.5L176.4 215.7L175.5 216.4L173.0 211.8L170.6 210.1L158.7 209.8L156.3 215.5L155.0 214.9L153.8 215.6L153.3 218.0L154.1 219.3L153.2 219.6L152.5 221.7L150.7 222.1L150.7 225.5L148.2 226.1L148.0 225.0L143.6 226.4L140.1 226.1L138.9 226.8L138.5 230.3L136.1 233.0L134.8 232.9L133.9 230.2L131.7 231.5L130.8 231.2L131.0 233.6L128.5 232.7L125.5 235.4L122.8 232.7L116.1 232.6L116.3 234.1L114.7 236.2L110.5 238.1L109.9 239.1L102.0 235.6L99.1 235.1L81.8 226.9L60.3 207.9L21.5 203.2L2.9 196.2L5.3 190.7L9.8 188.1L11.9 187.8L13.1 186.5L13.5 185.1L11.3 180.9L11.5 179.1L15.8 173.2L15.8 170.2L17.3 167.1L16.9 165.4L18.0 164.5L20.8 163.6L24.6 160.8L24.8 159.7L28.5 158.4L32.3 155.1L36.3 154.4L36.5 155.4L38.2 154.7L38.0 154.0L41.0 154.2L41.5 153.1L42.6 153.0L42.7 153.7L45.9 153.0L45.8 152.4L46.7 153.0L48.7 149.8L50.7 150.1L51.1 148.9L51.5 150.0L52.7 149.1L53.4 150.1L54.4 149.1L55.9 149.9L56.1 149.2L56.5 151.5L57.2 151.3L58.1 152.7L59.4 151.1L60.6 152.6L61.7 151.7L70.3 105.3L70.3 101.5L70.0 99.4L66.8 95.4L67.8 93.7L67.0 91.6L62.3 88.8L60.2 86.5L60.4 74.7L64.0 74.5L66.0 72.9L67.1 73.6L69.0 71.9L70.9 73.8L74.6 73.2L73.6 71.9L74.3 69.6L72.3 66.7L65.9 66.5L65.5 65.7L63.5 66.3L63.5 56.3L68.0 55.1L70.4 56.2L89.3 56.1L87.6 54.6L88.5 52.5L89.6 52.1L92.6 54.5L95.2 51.2L97.5 51.5L100.8 47.9L101.8 48.6Z","x":142.9,"y":149.4,"g":1},
"AP":{"d":"M365.8 72.4L361.0 73.7L358.7 78.7L356.9 80.7L352.9 82.4L350.6 84.9L348.6 85.4L341.7 95.6L341.2 100.4L339.5 101.9L337.3 102.1L336.8 101.3L335.8 103.1L335.1 103.0L334.8 101.7L330.1 100.2L330.5 97.4L329.7 96.7L328.4 97.1L328.6 92.9L326.8 92.8L325.9 90.8L326.1 88.4L323.5 85.9L322.3 85.8L318.7 77.6L319.7 72.7L315.0 68.8L314.3 65.6L315.2 65.2L314.8 63.9L313.1 64.3L313.0 62.2L311.4 62.4L311.4 61.2L308.3 61.5L305.9 59.5L304.7 60.0L303.8 57.5L303.1 57.8L301.3 56.0L294.6 55.4L293.6 50.9L294.3 48.6L292.6 45.1L294.6 44.4L295.5 44.8L295.9 46.7L296.8 46.5L299.3 48.5L303.1 49.0L306.9 48.0L307.0 47.1L309.6 45.8L309.9 46.9L312.9 47.8L316.1 46.2L317.8 47.7L317.0 48.9L322.8 48.9L328.1 43.7L328.1 41.8L330.8 37.4L331.5 33.3L333.7 31.2L337.8 23.6L342.0 19.4L344.1 13.2L348.1 17.1L349.2 21.0L350.8 21.9L350.5 28.9L351.4 33.8L354.4 41.7L354.2 44.1L354.9 43.8L355.5 44.7L356.5 49.7L360.4 48.7L363.6 55.0L368.5 56.7L368.9 64.6Z","x":337.2,"y":60.7,"g":1},
"BA":{"d":"M530.8 219.2L532.1 221.6L535.0 221.9L536.3 223.1L538.6 222.8L540.7 225.8L542.6 223.4L543.7 224.0L543.0 225.3L543.8 226.4L546.4 226.5L546.0 228.4L547.8 232.8L551.0 234.1L550.3 235.7L551.1 236.4L550.3 237.5L551.4 239.1L551.0 240.7L553.5 242.0L555.0 247.3L553.1 248.8L553.8 253.0L551.1 254.0L550.2 253.0L547.7 253.4L547.5 256.5L549.4 258.1L550.0 260.6L551.3 261.0L550.7 262.4L551.3 264.1L552.9 264.6L553.8 266.0L555.9 266.9L556.3 266.1L558.3 266.6L561.0 264.9L556.7 273.6L550.2 283.8L545.6 288.8L543.4 289.8L542.0 289.5L542.3 288.3L541.5 288.5L540.9 290.0L536.2 294.0L537.3 296.6L536.6 298.4L537.3 300.0L536.1 300.2L535.6 301.4L536.7 304.4L534.6 316.6L536.6 332.0L537.8 334.9L535.3 341.2L535.5 343.0L533.4 349.1L533.9 351.3L532.4 355.6L532.6 362.1L533.5 363.8L531.5 366.8L528.8 367.9L526.7 370.1L525.4 374.3L516.9 368.5L516.3 367.6L517.6 366.4L516.9 364.6L510.8 359.4L511.9 357.4L511.6 353.9L512.8 351.1L516.0 351.4L516.4 349.9L515.0 349.6L516.1 346.2L517.9 346.3L518.8 343.8L519.3 344.3L520.4 342.1L521.6 341.6L522.5 338.9L521.6 337.1L520.2 337.1L519.0 335.5L517.7 335.6L516.8 334.0L514.5 334.3L513.2 333.2L511.7 334.0L509.6 331.8L508.6 332.6L507.8 331.5L506.8 332.3L505.6 331.5L502.8 333.5L499.9 333.0L499.5 329.2L492.7 322.9L488.3 324.2L487.0 322.6L485.6 323.2L482.9 322.2L475.3 316.6L471.7 315.7L467.7 316.9L466.3 318.3L465.5 317.3L460.9 315.8L462.4 310.8L455.8 309.1L450.4 310.8L446.3 313.3L446.2 314.6L442.5 317.3L440.6 317.2L436.8 320.5L435.1 320.4L432.7 323.0L429.2 323.5L427.3 325.4L426.6 324.3L427.7 323.8L429.0 320.7L427.7 318.6L428.8 313.9L428.2 312.1L429.5 312.0L429.9 311.0L427.5 308.5L424.4 307.0L425.3 305.6L424.4 304.5L424.9 303.0L424.1 302.2L425.0 301.7L424.5 300.5L426.0 298.9L424.9 298.5L424.7 296.4L427.8 294.0L424.4 295.1L423.6 294.4L423.5 291.1L426.7 288.3L423.8 288.8L424.2 283.0L426.1 281.4L424.6 281.5L423.1 279.1L422.4 274.3L425.8 272.1L422.7 271.6L423.7 267.8L427.2 267.9L421.1 266.0L419.0 262.4L421.2 260.9L422.4 257.8L424.1 256.4L424.6 254.0L425.5 253.4L425.2 252.3L431.1 249.0L431.1 247.8L432.4 247.5L433.1 246.2L432.7 244.5L434.5 243.7L437.7 249.1L436.9 250.1L437.2 252.1L440.0 255.0L442.7 255.3L444.8 256.7L448.9 254.0L448.9 252.8L450.2 251.9L451.4 252.3L454.0 250.7L455.0 251.9L457.1 252.1L458.7 248.5L460.4 248.8L462.6 243.4L463.8 243.2L464.2 242.1L463.5 240.6L464.4 239.5L462.4 238.2L461.4 234.9L467.0 230.4L470.1 232.9L472.6 232.1L474.8 232.7L475.2 234.4L478.0 235.9L478.7 234.6L482.2 234.0L484.9 231.2L492.2 230.0L493.9 226.4L496.7 225.5L499.2 221.5L503.3 221.5L503.1 222.2L504.7 223.7L506.2 223.6L508.0 227.7L510.1 228.7L508.4 233.4L510.8 233.8L513.9 231.8L515.2 231.8L516.4 227.2L518.3 227.9L520.9 226.9L522.2 225.1L521.9 223.5L525.3 222.8L525.0 220.8L529.4 218.9Z","x":493.9,"y":281.2,"g":1},
"CE":{"d":"M562.4 160.1L556.4 161.6L552.4 170.7L549.7 173.4L550.2 174.4L548.4 177.8L546.3 180.0L544.1 180.0L542.1 183.1L541.7 184.8L543.0 185.1L540.6 189.7L541.5 191.0L539.2 193.1L539.2 194.4L540.7 195.2L540.4 197.5L542.7 199.1L540.9 203.4L536.5 206.5L536.2 207.8L535.3 207.4L534.2 208.1L533.6 206.0L528.4 203.6L528.6 202.0L527.2 202.1L524.7 199.8L518.4 200.5L516.0 199.2L513.7 200.3L512.3 199.5L512.8 196.4L514.1 194.6L513.7 192.3L514.6 191.4L509.1 189.0L508.2 186.8L508.3 183.6L506.4 179.3L506.1 165.7L503.0 163.5L501.2 160.7L501.8 159.2L501.2 158.9L502.3 157.5L501.3 156.0L503.6 149.6L502.8 148.9L503.3 147.6L501.1 147.5L501.6 146.0L500.4 144.2L501.3 142.4L499.8 141.9L499.3 140.1L500.4 138.9L498.5 136.9L501.1 132.5L500.1 129.8L511.2 128.6L512.7 127.7L521.2 128.8L531.7 134.6L540.7 141.8L543.6 142.2L550.7 150.9L554.4 153.3L557.1 156.8L561.2 158.1Z","x":526.2,"y":164.2,"g":1},
"DF":{"d":"M405.1 329.2L406.8 329.2L408.3 330.6L407.4 336.7L408.4 337.9L393.6 337.9L393.5 334.5L394.7 333.1L394.1 332.2L394.8 329.2Z","x":400.9,"y":333.6,"g":0},
"ES":{"d":"M512.4 367.1L516.9 368.5L525.4 374.1L524.2 380.0L525.0 390.5L523.3 394.9L520.2 396.6L518.2 399.7L517.3 403.9L515.8 405.0L513.8 410.6L512.2 410.9L512.3 412.1L510.7 413.8L510.4 413.3L508.7 414.2L505.6 421.1L503.6 419.8L502.1 420.3L496.3 419.2L493.7 418.0L494.1 414.3L491.5 412.5L492.6 410.7L491.9 410.3L492.8 407.4L492.0 405.7L493.4 403.8L499.2 403.5L500.3 399.7L502.2 398.7L502.4 395.3L504.4 393.6L504.3 392.4L505.8 392.1L506.4 389.5L505.9 386.9L504.0 385.4L504.7 384.2L501.3 382.3L501.4 381.4L503.5 382.1L506.3 381.7L505.9 379.7L504.2 378.8L504.7 376.1L502.2 375.7L502.6 373.7L504.1 371.4L506.6 370.5L508.5 371.3L506.8 368.3L507.9 368.0L509.5 369.2Z","x":510.0,"y":393.7,"g":0},
"GO":{"d":"M364.8 280.2L363.7 282.2L364.3 282.6L362.6 284.5L362.8 287.0L368.6 289.0L376.9 293.9L377.4 290.6L380.7 286.2L381.4 288.0L382.9 288.9L384.9 286.5L386.7 289.4L388.6 290.5L388.9 294.6L390.1 291.6L391.1 294.2L395.6 292.0L395.3 294.4L396.9 293.3L398.4 294.5L400.5 294.5L402.8 297.0L403.6 295.4L402.9 292.9L403.4 291.2L404.6 291.5L404.5 292.5L405.8 292.5L406.6 294.1L407.4 293.2L408.8 293.8L409.7 292.6L413.5 291.6L417.0 289.1L421.5 289.1L422.1 286.8L422.9 287.4L422.9 289.4L426.7 288.3L423.5 291.1L424.2 295.1L427.8 294.0L424.7 296.4L424.9 298.5L426.0 298.9L424.5 300.5L425.0 301.7L424.1 302.2L424.9 303.0L424.4 304.5L425.3 305.6L424.4 307.0L427.5 308.5L429.9 311.1L429.5 312.0L428.2 312.1L428.8 313.9L428.3 318.0L427.1 320.2L424.1 320.1L423.5 318.3L420.8 316.6L419.8 317.9L420.8 322.1L418.7 322.6L415.8 321.4L414.3 322.2L414.9 323.0L414.1 324.9L415.7 326.4L413.9 330.1L415.4 331.0L416.0 335.3L411.8 336.5L411.0 335.9L408.3 337.7L407.4 335.2L408.3 330.6L406.8 329.2L394.8 329.2L394.1 332.2L394.7 333.0L393.5 334.6L393.6 337.9L408.5 338.0L407.8 339.2L408.1 341.0L406.1 345.1L409.3 347.6L410.1 351.5L411.2 352.7L407.8 355.6L406.4 358.5L405.3 358.1L404.9 360.1L406.2 361.5L408.1 361.2L409.1 362.6L407.4 366.1L408.8 369.7L405.0 372.4L404.5 372.0L403.6 374.1L398.5 376.7L398.1 375.8L393.8 374.0L393.1 374.9L389.3 373.9L385.4 374.8L383.5 373.6L381.4 375.4L379.4 375.3L376.6 379.0L374.4 376.6L372.7 378.3L370.5 379.0L367.0 378.3L366.0 379.4L362.5 379.8L359.4 383.6L358.9 386.3L356.9 386.8L354.8 389.2L353.8 391.3L354.3 392.5L351.5 391.0L350.6 389.5L345.5 387.3L341.9 386.8L337.5 384.1L335.4 383.9L333.8 382.2L331.5 381.9L329.7 379.7L322.6 378.9L321.9 377.4L325.0 374.3L319.8 373.7L319.1 370.1L320.2 368.6L317.5 363.9L317.5 361.2L318.0 357.7L320.5 354.1L321.1 350.7L324.6 348.8L325.7 347.2L325.3 346.5L327.0 345.5L327.4 344.5L326.1 343.4L326.2 341.9L328.2 341.3L328.6 339.3L331.6 338.2L332.8 335.4L336.4 335.3L337.4 334.1L338.5 334.3L340.3 331.3L340.0 329.8L340.8 330.0L341.2 328.9L341.9 324.1L343.7 322.4L346.8 320.8L348.2 321.8L350.6 319.9L352.5 313.8L352.3 310.0L353.2 307.2L354.5 306.8L353.9 301.2L357.9 294.5L357.9 290.6L359.2 289.2L359.9 285.0L362.3 281.8Z","x":373.0,"y":337.7,"g":1},
"MA":{"d":"M444.4 103.8L445.5 104.9L444.0 105.9L443.5 105.1L444.3 105.5ZM434.5 101.4L434.1 104.9L433.1 105.1ZM430.9 100.1L429.8 103.1L430.8 102.7L430.6 103.9L431.7 102.0L430.9 103.9L431.5 104.8L430.9 103.9L431.9 103.7L432.5 101.5L433.4 101.7L432.4 103.2L433.1 103.7L432.6 104.9L433.4 104.9L432.3 105.2L433.1 105.1L432.7 106.0L434.2 105.2L434.9 103.5L434.5 104.8L435.3 103.7L435.0 104.8L435.7 104.4L435.1 105.3L436.1 104.0L435.8 105.9L437.5 104.0L436.3 106.2L436.9 107.6L436.3 107.8L437.0 108.0L436.9 106.5L437.4 106.0L437.6 107.1L438.7 105.1L438.3 104.4L439.2 104.8L439.3 106.1L437.8 107.0L438.5 107.1L437.7 108.5L438.6 106.9L439.2 107.2L437.6 110.6L438.3 110.2L438.4 111.1L439.0 108.9L440.0 109.3L441.5 106.7L442.1 108.1L442.2 105.1L442.3 107.5L442.7 106.1L442.6 107.6L444.6 106.9L444.6 107.6L446.6 106.0L446.4 107.1L445.5 107.0L445.3 109.1L446.5 108.5L446.8 109.8L447.1 108.6L447.8 109.1L448.4 108.2L447.9 109.3L448.6 108.3L449.2 109.3L449.0 109.9L447.1 110.0L447.0 111.3L447.2 110.7L448.4 111.1L446.1 112.5L448.4 112.1L449.0 111.2L448.4 112.4L449.1 110.8L450.0 111.2L449.3 113.0L451.0 112.7L449.8 113.7L451.1 113.4L451.6 114.3L451.4 117.5L453.0 118.6L453.6 120.6L452.8 121.8L454.1 123.2L458.7 121.5L458.7 122.4L457.3 122.7L459.4 122.6L459.4 124.3L461.7 121.0L462.9 121.2L463.2 119.8L465.0 118.7L466.8 121.1L468.6 120.4L471.6 121.2L478.2 123.9L479.1 125.5L478.8 124.1L482.4 126.5L486.2 125.8L488.9 126.2L489.3 127.3L489.4 126.6L492.4 126.6L491.8 129.2L492.8 130.4L490.6 134.1L487.7 135.4L488.2 135.9L486.6 138.0L482.0 138.3L479.3 141.8L478.6 145.5L474.6 150.6L474.9 152.9L476.7 154.6L475.1 159.4L476.6 161.8L477.5 165.7L477.0 168.3L472.9 172.8L473.2 179.5L476.5 182.4L477.0 184.0L475.6 189.2L474.1 190.7L470.6 190.8L468.0 192.0L463.4 189.7L458.6 190.7L458.0 191.6L457.3 191.4L457.5 192.2L455.1 194.5L454.4 196.3L450.4 198.1L448.5 200.7L446.6 200.2L444.9 201.9L438.6 203.7L436.8 205.1L432.1 220.0L429.5 222.7L428.6 225.0L430.1 231.6L431.8 233.8L430.9 235.1L430.3 243.8L429.3 246.1L428.4 246.2L428.0 244.8L425.9 245.4L422.8 244.7L421.5 242.8L420.7 238.9L418.6 237.8L420.2 235.0L419.9 233.8L418.3 232.4L416.8 232.7L414.3 227.2L412.1 227.2L413.3 224.3L414.5 223.8L414.5 219.6L416.1 216.6L420.7 214.6L421.0 210.1L418.4 208.7L412.5 211.2L405.4 201.4L404.1 201.4L405.8 200.2L405.4 199.1L403.2 199.3L401.7 197.5L403.3 196.7L405.1 194.1L407.4 182.9L405.5 171.1L401.8 168.8L400.2 168.7L399.6 166.9L395.1 166.9L392.3 165.4L389.8 165.8L388.6 168.1L387.5 167.6L386.3 168.3L401.0 156.2L402.7 156.6L403.8 155.8L405.9 151.9L407.5 150.8L408.3 147.7L411.8 144.7L412.6 140.0L414.0 138.6L414.0 137.1L416.0 136.0L417.4 133.5L417.0 133.0L418.1 132.6L418.1 129.2L419.7 128.6L418.3 126.2L422.1 123.6L422.2 121.1L421.3 121.1L421.9 119.0L422.7 119.3L424.2 117.8L425.3 112.6L423.5 111.5L426.1 110.1L425.9 106.8L427.0 104.6L426.2 103.0L427.6 101.5L427.8 102.1L428.7 100.1L429.3 103.2Z","x":439.5,"y":163.8,"g":1},
"MG":{"d":"M455.9 309.2L462.4 310.8L460.9 315.8L465.5 317.3L466.3 318.3L467.7 316.9L471.7 315.7L475.3 316.6L482.9 322.2L485.6 323.2L487.0 322.6L488.3 324.2L492.7 322.9L499.5 329.2L499.9 333.0L502.8 333.5L505.6 331.5L506.8 332.3L507.8 331.5L508.6 332.6L509.5 331.8L511.7 334.0L513.2 333.2L514.5 334.3L516.8 334.0L517.7 335.6L519.0 335.5L520.2 337.1L521.6 337.1L522.5 338.9L521.6 341.6L520.4 342.1L519.3 344.3L518.8 343.8L517.9 346.3L516.1 346.2L515.0 349.6L516.4 349.9L516.0 351.4L512.8 351.1L511.6 353.9L511.9 357.4L510.8 359.4L516.9 364.6L517.6 366.4L516.3 367.6L516.9 368.5L512.2 367.1L509.5 369.2L507.9 368.0L506.8 368.3L508.5 371.3L506.6 370.5L504.1 371.4L502.6 373.7L502.2 375.7L504.7 376.1L504.2 378.8L505.9 379.7L506.3 381.7L503.5 382.1L501.4 381.4L501.3 382.3L504.7 384.2L503.9 385.0L506.1 387.6L505.8 391.9L505.4 392.6L504.3 392.4L504.4 393.6L502.4 395.3L502.2 398.7L500.3 399.7L499.2 403.5L493.4 403.8L492.0 405.7L492.8 407.4L491.9 410.3L492.6 410.7L490.1 415.3L487.4 415.9L488.5 416.9L486.5 419.2L486.8 420.3L485.2 423.6L485.8 424.2L484.0 426.2L485.6 427.6L476.2 431.5L473.3 433.7L472.3 433.9L472.4 432.6L470.6 432.3L465.8 433.6L462.7 433.2L455.5 436.4L452.1 436.3L449.0 438.2L442.4 439.8L437.6 442.5L436.5 441.5L435.5 442.5L434.9 441.7L433.6 442.5L433.7 441.4L432.8 441.3L433.1 442.5L431.3 443.4L432.9 444.4L431.8 445.7L430.2 446.1L429.8 445.1L426.3 446.8L426.3 445.8L423.2 446.5L422.6 446.0L423.3 444.2L421.1 443.3L422.4 442.7L422.2 440.7L418.6 439.0L417.4 437.0L418.1 435.0L419.3 434.3L417.4 433.4L419.1 432.3L417.9 429.6L419.0 427.0L420.5 426.0L420.7 423.8L419.0 423.3L418.3 422.1L417.7 422.7L416.8 422.0L413.0 423.0L411.0 416.1L409.5 414.5L411.7 410.7L410.8 408.7L408.7 407.6L409.2 403.1L406.4 401.3L406.0 399.9L403.4 401.3L402.4 400.2L400.1 400.3L399.4 402.5L398.2 401.0L397.5 402.4L396.1 402.8L394.2 400.9L394.2 402.7L385.2 403.0L384.1 407.5L383.0 406.7L382.7 403.1L381.5 402.9L380.7 404.8L379.0 405.3L377.8 402.1L378.7 400.0L375.7 400.2L374.1 399.0L368.9 399.6L362.1 398.4L360.0 397.0L351.9 401.8L351.4 395.5L353.1 393.8L352.5 392.3L354.6 392.4L353.8 391.3L354.8 389.2L356.9 386.8L358.9 386.3L359.4 383.6L362.5 379.8L366.0 379.4L367.0 378.3L370.5 379.0L372.7 378.3L374.4 376.6L376.6 379.0L379.4 375.3L381.4 375.4L383.5 373.6L385.4 374.8L389.3 373.9L393.1 374.9L393.8 374.0L398.1 375.8L398.5 376.7L403.6 374.1L404.5 372.0L405.0 372.4L408.8 369.7L407.4 366.1L409.1 362.6L408.1 361.2L405.5 361.3L404.9 359.1L405.3 358.1L406.4 358.5L407.8 355.6L411.2 352.7L410.1 351.5L409.3 347.6L406.1 345.1L408.2 340.8L407.8 339.2L408.6 337.4L409.8 337.3L411.0 335.9L411.8 336.5L416.1 335.1L415.4 331.0L413.9 330.1L415.7 326.4L414.1 324.9L414.9 323.0L414.4 322.0L415.8 321.4L418.7 322.6L420.6 322.2L419.8 317.9L421.2 316.6L423.5 318.3L424.1 320.1L428.4 319.7L428.8 321.9L426.6 324.3L427.6 325.4L429.2 323.5L432.7 323.0L435.1 320.4L436.8 320.5L440.6 317.2L442.5 317.3L446.2 314.6L446.3 313.3L450.4 310.8Z","x":448.7,"y":376.0,"g":1},
"MS":{"d":"M273.8 355.7L276.3 357.4L280.9 358.3L282.7 360.6L286.2 361.5L288.8 363.3L292.8 362.8L297.1 360.4L298.6 360.7L300.9 363.4L304.7 362.8L305.4 360.7L310.5 356.6L310.5 363.5L308.2 364.1L306.8 367.6L313.9 369.4L314.6 368.5L320.2 369.3L319.1 370.1L320.2 374.2L322.3 373.5L325.0 374.3L321.9 377.4L322.7 379.0L329.7 379.7L331.5 381.9L333.8 382.2L335.4 383.9L337.5 384.1L341.9 386.8L345.5 387.3L350.6 389.5L352.9 391.9L353.1 393.8L352.1 394.0L351.2 396.2L351.9 401.8L351.0 404.2L346.7 406.1L342.8 410.7L342.4 415.4L338.5 418.5L338.7 421.7L337.1 424.3L335.2 424.9L335.5 427.7L329.1 435.6L321.3 440.0L318.3 443.7L312.0 447.3L310.1 453.1L306.3 455.3L304.5 463.1L301.6 465.0L299.3 462.4L295.7 460.9L289.8 463.8L289.1 463.3L287.2 464.1L284.3 463.2L284.0 459.4L282.5 457.9L282.1 455.7L283.0 454.0L282.2 453.1L282.7 451.1L281.6 450.4L280.5 445.7L281.3 442.6L277.6 436.6L272.2 436.6L269.8 434.9L269.4 433.4L267.6 433.7L265.7 436.3L264.6 435.6L262.5 437.0L260.1 435.7L254.3 435.9L251.2 435.0L250.7 433.7L247.8 434.6L244.7 433.0L246.1 430.3L245.2 429.7L246.6 427.2L245.3 424.7L247.0 421.7L246.0 420.8L247.1 419.8L246.8 417.0L247.5 415.4L245.9 414.7L246.9 413.6L245.4 413.0L246.9 412.2L246.0 411.0L245.0 411.6L244.8 407.5L243.5 406.4L243.3 404.5L242.3 404.6L242.9 403.6L242.2 403.2L245.4 400.8L246.3 401.2L246.9 400.0L242.8 396.7L248.1 385.2L249.5 384.8L249.1 383.0L248.4 383.0L251.5 372.6L253.1 372.5L251.3 370.9L247.9 361.8L249.2 361.5L249.6 364.3L253.2 367.2L254.3 366.0L259.4 364.5L260.4 362.1L262.0 361.4L262.7 359.1L264.2 357.9L265.5 358.3L267.7 357.5L268.6 358.2Z","x":293.1,"y":405.7,"g":1},
"MT":{"d":"M251.0 221.5L251.0 222.3L253.5 222.7L254.2 224.3L257.2 225.1L259.5 227.7L259.2 229.0L262.8 230.1L263.8 232.6L265.1 232.0L363.8 239.5L361.2 244.1L360.8 247.7L358.0 252.5L357.9 258.9L355.9 264.8L355.9 266.4L357.2 267.3L356.2 269.6L357.5 271.9L356.7 276.9L357.8 280.5L356.4 283.4L357.3 284.0L357.7 286.7L358.3 286.4L359.6 287.7L357.9 290.6L357.9 294.5L353.9 301.2L354.5 306.8L353.2 307.2L352.3 310.0L352.5 313.8L350.6 319.9L348.2 321.8L346.8 320.8L343.7 322.4L341.9 324.1L341.2 328.9L340.8 330.0L340.0 329.8L340.3 331.3L338.5 334.3L337.4 334.1L336.4 335.3L332.8 335.4L331.6 338.2L328.6 339.3L328.2 341.3L326.2 341.9L326.1 343.4L327.4 344.5L326.9 345.8L325.3 346.5L325.7 347.2L324.6 348.8L321.1 350.7L320.5 354.1L318.0 357.7L317.5 363.9L320.2 369.3L314.6 368.5L313.9 369.4L306.8 367.6L308.2 364.1L310.5 363.5L310.5 356.6L305.4 360.7L304.7 362.8L300.9 363.4L298.6 360.7L297.1 360.4L292.8 362.8L288.8 363.3L286.2 361.5L282.7 360.6L280.9 358.3L276.3 357.4L273.7 355.6L268.6 358.2L267.7 357.5L265.5 358.3L264.2 357.9L262.7 359.1L262.0 361.4L260.4 362.1L259.4 364.5L254.3 366.0L253.2 367.2L249.6 364.3L248.9 361.3L248.6 361.9L246.6 360.1L244.1 360.7L238.7 355.8L237.5 351.9L237.6 348.2L239.7 344.9L239.9 341.3L238.2 342.2L211.5 341.3L210.5 328.8L205.5 323.0L210.4 322.8L210.0 315.2L209.3 315.1L206.6 308.4L208.3 305.2L206.6 303.1L207.0 302.2L203.2 300.4L204.5 298.6L208.2 296.8L209.8 290.8L213.0 287.7L213.1 283.5L215.2 281.4L216.4 281.3L217.6 279.1L215.8 277.6L214.4 272.3L212.5 271.2L212.4 267.2L215.3 264.4L215.4 263.2L214.4 259.7L211.1 259.7L209.6 258.8L208.8 259.6L207.1 257.7L190.4 257.6L191.6 254.7L191.8 248.7L189.7 244.5L191.1 240.0L190.1 237.5L191.6 236.1L189.2 230.5L190.8 230.0L190.4 227.6L191.7 224.9L189.9 223.0L238.4 222.9L239.8 221.7L238.1 221.5L240.4 212.4L238.9 207.8L241.7 204.3L241.5 201.7L242.7 200.0L245.2 202.9L247.4 209.9L250.2 213.7L249.6 216.9Z","x":276.8,"y":288.7,"g":1},
"PA":{"d":"M291.4 42.6L294.3 48.6L293.6 50.9L294.6 55.4L301.3 56.0L303.1 57.8L303.8 57.5L304.7 60.0L305.9 59.5L308.3 61.5L311.4 61.2L311.4 62.4L313.0 62.2L313.1 64.3L314.8 63.9L315.2 65.2L314.3 65.6L315.0 68.8L319.7 72.7L318.7 77.6L322.3 85.8L323.5 85.9L326.1 88.4L325.9 90.8L326.8 92.8L328.6 92.9L328.4 97.1L329.7 96.7L330.5 97.4L330.1 100.2L334.8 101.7L335.1 103.0L335.8 103.1L336.8 101.3L337.3 102.1L339.5 101.9L341.2 100.4L341.7 95.6L348.6 85.4L350.6 84.9L352.9 82.4L356.9 80.7L358.7 78.7L360.6 74.0L364.8 72.4L365.8 72.4L366.6 74.5L366.3 78.2L371.2 78.4L372.2 77.7L376.5 83.3L383.6 87.1L391.5 87.6L391.9 89.3L390.6 91.5L397.8 94.1L397.9 94.7L399.4 92.3L400.3 92.8L400.3 94.3L400.8 92.3L401.4 93.7L402.4 92.0L403.6 94.7L404.3 92.8L405.1 93.5L406.7 92.9L406.9 94.0L408.2 93.0L408.7 93.8L409.9 93.6L409.4 94.8L410.7 94.2L410.6 95.8L411.8 94.0L412.3 96.3L412.5 95.1L413.8 94.8L414.1 97.4L415.3 95.2L415.9 97.9L416.7 96.5L417.1 98.1L417.4 96.6L418.1 97.2L418.7 96.0L419.1 97.0L418.1 99.0L419.5 99.0L420.0 97.9L420.2 99.0L420.6 97.6L421.9 97.1L420.8 99.0L421.3 100.2L422.1 99.7L421.9 100.4L422.5 99.2L423.8 100.7L423.6 99.6L424.1 100.1L424.4 98.1L425.3 97.6L424.3 102.1L425.8 99.3L425.8 101.9L425.9 100.7L426.2 101.5L427.3 99.7L426.1 103.7L427.0 104.6L425.9 106.8L426.1 110.1L423.5 111.5L425.3 112.6L424.2 117.8L422.7 119.3L421.9 119.0L421.3 121.1L422.2 121.1L422.1 123.6L418.3 126.2L419.7 128.6L418.1 129.2L418.1 132.6L417.0 133.0L417.4 133.5L416.0 136.0L414.0 137.1L414.0 138.6L412.6 140.0L411.8 144.7L408.3 147.7L407.5 150.8L405.9 151.9L403.8 155.8L402.7 156.6L401.0 156.2L386.3 168.3L388.7 169.5L392.0 169.1L393.2 171.1L395.7 172.3L395.2 174.0L393.4 174.7L394.3 177.8L392.7 178.7L393.2 180.5L391.2 181.4L392.0 184.6L390.2 184.2L388.5 185.8L387.7 189.1L382.3 191.1L379.3 193.3L379.7 198.2L376.7 203.1L377.5 205.2L380.3 207.3L379.3 213.1L378.2 216.3L373.5 223.6L371.1 224.7L366.7 230.8L363.8 239.5L265.1 232.0L263.7 232.6L262.8 230.1L259.2 229.0L259.5 227.7L257.0 224.9L254.2 224.3L253.5 222.7L251.0 222.3L249.6 216.9L250.2 213.7L247.4 209.9L246.3 205.1L243.9 200.8L242.2 199.4L242.0 197.3L237.7 191.9L237.7 189.2L240.9 185.8L269.2 122.5L268.3 121.9L269.5 119.5L272.0 118.3L273.9 115.8L269.1 118.0L267.3 117.4L265.0 118.6L263.6 117.9L264.2 115.6L262.4 115.6L259.5 113.8L257.6 110.8L254.1 110.8L253.9 110.1L252.0 109.7L250.9 108.4L249.7 108.8L249.2 107.4L245.4 105.8L244.5 101.1L242.3 103.0L238.2 99.8L238.1 97.5L234.0 94.3L233.7 90.6L231.4 89.0L231.1 64.1L232.2 65.0L233.5 64.5L234.1 63.0L237.2 63.4L237.8 61.8L237.0 60.4L238.9 60.2L239.8 58.2L242.3 58.8L242.8 59.8L244.7 59.7L244.9 57.3L248.2 56.1L251.6 56.7L253.5 53.3L255.4 51.9L256.6 52.8L258.8 51.4L259.9 53.2L261.3 53.0L263.2 54.2L264.4 53.0L266.5 53.3L268.5 52.5L275.7 54.5L276.9 53.6L276.9 51.2L274.7 48.2L273.3 47.6L274.0 45.9L275.1 46.4L275.7 43.5L279.7 45.5L284.8 45.2L285.8 43.7Z","x":320.3,"y":146.5,"g":1},
"PB":{"d":"M562.7 179.1L563.6 179.4L563.8 181.0L560.5 184.1L560.1 186.8L558.8 187.3L558.8 189.9L561.9 189.6L562.6 191.7L566.2 189.9L566.9 191.2L568.8 190.2L570.4 191.9L569.8 193.5L570.5 194.2L573.8 191.4L573.0 189.9L573.5 188.1L574.9 188.5L573.4 185.8L573.9 184.3L574.9 184.4L575.5 183.3L577.0 183.3L577.7 185.6L580.3 185.0L581.9 186.4L586.8 185.7L594.2 187.5L597.3 186.3L597.8 190.8L600.0 196.9L599.5 203.2L597.4 203.0L595.6 200.8L593.1 200.4L590.4 202.0L589.6 201.5L588.7 204.8L581.6 207.4L578.2 206.6L577.7 207.6L575.0 207.4L574.7 209.0L573.1 208.7L572.2 209.5L571.9 212.1L566.4 215.1L563.8 213.0L563.3 209.7L560.8 209.9L563.9 206.8L563.6 203.8L566.5 202.1L565.8 200.6L562.7 198.8L558.6 200.3L558.1 202.0L555.4 203.2L554.5 204.9L553.1 204.8L551.4 206.8L550.3 206.3L549.7 207.6L548.4 206.8L546.5 207.6L545.4 205.2L541.8 206.4L539.9 204.1L542.7 199.1L540.4 197.5L540.7 195.2L539.2 193.6L541.5 191.0L540.6 189.7L543.0 185.1L541.7 184.8L543.9 183.9L543.5 184.9L546.5 186.6L549.1 186.9L554.6 183.2L554.9 181.6L559.2 180.9Z","x":568.9,"y":196.4,"g":1},
"PE":{"d":"M517.3 200.1L524.9 199.9L527.2 202.1L528.6 202.0L528.4 203.6L533.6 206.0L534.2 208.1L535.3 207.4L536.2 207.8L536.5 206.5L540.0 204.3L541.9 206.4L545.4 205.2L546.5 207.6L548.4 206.8L549.7 207.6L550.3 206.3L551.4 206.8L553.1 204.8L554.5 204.9L555.4 203.2L558.1 202.0L558.6 200.3L562.7 198.8L565.8 200.6L566.5 202.1L563.6 203.8L563.9 206.8L560.8 209.9L563.3 209.7L563.8 213.0L566.4 215.1L571.9 212.1L572.2 209.5L573.1 208.7L574.7 209.0L575.0 207.4L577.7 207.6L578.2 206.6L581.6 207.4L588.7 204.8L589.1 201.7L590.4 202.0L593.1 200.4L595.8 200.8L597.4 203.0L599.4 203.2L599.6 209.0L597.8 215.9L596.0 217.7L596.5 218.4L597.1 217.6L596.8 219.3L594.5 224.8L589.6 223.2L585.4 224.9L584.7 223.8L583.1 223.9L579.6 225.5L579.7 226.6L577.5 227.8L578.1 228.9L574.8 229.5L572.6 231.5L570.9 230.5L568.2 230.4L567.0 232.2L564.6 230.0L563.7 230.5L563.2 229.6L562.6 230.0L561.4 227.7L559.0 226.6L558.7 225.6L555.5 226.0L554.6 223.9L551.2 228.5L549.5 228.9L547.3 231.4L546.0 228.4L546.4 226.5L545.7 226.0L544.6 226.8L543.2 225.9L543.6 223.8L542.2 223.5L541.1 226.0L538.6 222.8L536.3 223.1L535.0 221.9L532.1 221.6L531.2 219.3L529.7 218.8L525.0 220.8L525.3 222.8L521.9 223.5L522.2 225.1L520.9 226.9L518.3 227.9L516.4 227.2L515.2 231.8L513.9 231.8L510.8 233.8L508.4 233.4L510.1 228.7L507.7 227.4L506.2 223.6L504.7 223.7L503.1 222.2L503.3 221.5L499.5 221.5L501.7 220.6L505.0 216.7L506.1 217.4L506.6 216.0L507.5 216.4L508.3 214.3L511.3 212.5L512.0 207.8L510.0 206.6L510.8 204.9L509.5 201.7L511.9 200.7L512.3 199.5L514.3 200.3L516.3 199.2Z","x":551.0,"y":215.5,"g":1},
"PI":{"d":"M493.5 127.9L495.9 129.6L500.1 129.8L501.1 131.2L498.5 136.9L500.4 138.9L499.3 140.1L499.8 141.9L501.3 142.4L500.4 144.2L501.6 146.0L501.1 147.5L503.3 147.6L502.8 148.9L503.6 149.6L501.3 156.0L502.3 157.5L501.2 158.9L501.8 159.2L501.2 160.7L503.0 163.5L506.1 165.7L506.4 179.3L508.3 183.6L508.2 186.8L509.1 189.0L514.6 191.4L513.7 192.3L514.1 194.6L512.8 196.4L511.9 200.7L509.4 202.1L510.8 204.9L510.0 206.6L512.0 207.8L511.3 212.5L508.3 214.3L507.8 216.1L506.6 216.0L506.1 217.4L505.0 216.7L501.7 220.6L499.2 221.5L496.7 225.5L493.9 226.4L492.2 230.0L484.9 231.2L482.2 234.0L478.7 234.6L478.0 235.9L475.2 234.4L474.8 232.7L472.6 232.1L470.1 232.9L467.0 230.4L461.4 234.9L462.4 238.2L464.4 239.5L463.5 240.6L464.2 242.1L463.8 243.2L462.6 243.4L460.4 248.8L458.7 248.5L457.1 252.1L455.0 251.9L454.0 250.7L451.4 252.3L450.2 251.9L448.9 252.8L448.9 254.0L444.8 256.7L442.7 255.3L440.0 255.0L437.2 252.1L436.9 250.1L437.7 249.1L434.8 243.9L432.6 244.5L431.6 246.3L429.3 246.1L429.1 245.5L430.4 243.3L430.9 235.1L431.8 233.8L430.1 231.6L428.6 225.0L429.5 222.7L432.1 220.0L432.6 216.9L434.9 212.8L436.4 205.9L438.6 203.7L444.9 201.9L446.6 200.2L448.5 200.7L450.4 198.1L454.4 196.3L458.3 190.8L463.4 189.7L468.0 192.0L470.6 190.8L474.1 190.7L475.6 189.2L477.0 184.0L476.5 182.4L473.2 179.5L472.9 172.8L477.0 168.3L477.5 165.7L476.6 161.8L475.1 159.4L476.7 154.6L474.9 152.9L474.6 150.6L478.6 145.5L479.3 141.8L482.0 138.3L486.6 138.0L488.2 135.9L487.7 135.4L489.7 134.9L492.4 131.5L492.0 127.5L492.5 127.0Z","x":474.8,"y":200.7,"g":1},
"PR":{"d":"M396.4 484.6L394.6 487.1L393.0 487.5L393.3 488.7L391.3 489.9L388.8 495.2L379.9 495.6L374.0 499.3L368.1 495.7L367.7 496.5L367.6 495.8L365.4 496.6L364.6 496.0L364.5 496.8L363.4 496.0L362.2 496.9L362.3 497.7L358.5 495.6L358.8 496.4L357.6 496.6L356.2 499.4L353.9 499.4L353.4 500.2L352.9 499.3L352.8 500.0L350.7 499.2L348.2 500.7L347.3 502.2L348.4 505.3L345.9 506.1L345.6 506.9L344.2 504.8L338.6 505.1L333.8 502.6L327.4 502.1L325.3 501.0L319.9 501.8L317.0 499.5L312.9 500.2L311.5 499.6L310.0 497.4L310.1 496.3L308.5 495.1L308.7 492.3L307.7 489.6L306.7 490.0L304.8 488.6L304.5 489.5L304.3 487.6L302.8 488.3L303.3 489.0L301.6 488.5L300.1 489.2L299.4 490.7L296.9 489.1L296.5 486.9L299.4 482.3L299.2 478.9L302.0 469.7L300.8 466.0L304.5 463.1L306.3 455.3L310.0 453.4L312.0 447.3L321.7 441.2L325.8 442.1L327.7 441.2L328.9 442.2L332.8 441.9L333.2 442.9L334.9 440.4L338.6 442.2L340.7 442.1L340.9 442.8L347.9 442.8L349.6 444.1L353.6 444.8L354.9 447.2L356.0 447.5L357.1 446.4L363.6 447.3L367.6 446.5L368.6 448.8L372.2 450.6L373.0 454.3L373.9 454.8L372.9 456.1L373.1 458.2L374.1 459.2L373.2 461.5L377.4 466.1L377.1 467.3L378.2 468.8L379.5 469.3L377.7 472.7L377.9 474.6L381.6 474.8L382.5 473.9L383.2 474.7L388.9 474.5L390.2 475.6L389.2 477.1L388.9 480.5L390.0 481.2L391.6 479.4L392.9 480.6L394.0 479.4L395.5 482.0L395.0 483.0L396.1 483.7L397.4 483.2Z","x":342.5,"y":474.0,"g":1},
"RJ":{"d":"M456.1 449.6L457.6 450.8L454.6 450.8L453.7 451.6L453.3 450.7ZM492.4 413.0L494.1 414.3L494.0 418.3L500.8 420.1L503.6 419.8L505.7 421.1L503.9 424.6L504.8 426.0L505.4 431.7L501.9 434.3L494.5 436.9L490.2 440.8L489.9 443.4L490.7 444.3L491.8 444.1L489.3 446.3L489.5 448.0L484.1 447.0L473.6 447.8L472.3 447.1L474.0 443.9L473.1 442.9L470.0 444.9L472.0 446.6L471.5 447.9L468.2 447.6L469.9 448.2L465.9 449.2L459.3 449.6L459.5 448.7L460.8 449.1L460.4 448.3L465.7 449.0L462.5 446.8L461.1 446.5L458.4 447.8L458.4 447.1L457.1 448.5L455.4 448.9L455.3 448.0L453.6 448.4L454.5 447.4L453.6 446.8L452.4 447.5L452.5 448.4L448.5 449.4L448.2 451.7L449.2 451.0L449.5 451.5L448.5 452.0L449.7 451.7L449.0 452.7L450.5 451.6L450.1 452.3L451.4 452.7L450.1 453.7L447.6 453.7L445.7 452.0L446.9 447.7L451.5 445.6L451.9 446.2L454.9 445.4L456.6 442.9L455.6 441.8L453.2 441.3L451.3 442.3L449.4 441.8L446.8 438.4L449.0 438.2L452.5 436.2L455.5 436.4L462.7 433.2L465.8 433.6L470.6 432.3L472.4 432.6L472.3 433.9L473.3 433.7L476.2 431.5L485.6 427.6L484.0 426.2L485.8 424.2L485.2 423.6L486.8 420.3L486.5 419.2L488.5 416.9L487.4 415.9L490.1 415.3L490.8 413.1Z","x":479.6,"y":435.3,"g":1},
"RN":{"d":"M597.3 186.4L594.2 187.5L586.8 185.7L581.9 186.4L580.3 185.0L577.7 185.6L577.0 183.3L575.5 183.3L574.9 184.4L573.9 184.3L573.4 185.8L574.9 188.5L573.5 188.1L573.0 189.9L573.8 191.4L570.5 194.2L569.8 193.5L570.4 191.9L568.8 190.2L566.9 191.2L566.2 189.9L562.6 191.7L561.9 189.6L558.8 189.9L558.8 187.3L560.1 186.8L560.7 183.7L563.7 181.3L563.6 179.4L562.4 179.0L559.2 180.9L554.9 181.6L554.6 183.2L549.1 186.9L546.5 186.6L543.5 184.9L543.9 183.9L542.3 184.3L542.0 183.6L544.1 180.0L546.3 180.0L548.4 177.8L550.2 174.4L549.7 173.4L552.4 170.7L556.4 161.6L562.4 160.1L563.7 161.7L566.8 161.5L570.9 164.3L579.4 164.3L581.8 163.4L589.4 165.3L592.8 170.4Z","x":571.2,"y":176.1,"g":1},
"RO":{"d":"M170.3 209.9L173.0 211.8L175.5 216.4L176.4 215.7L178.0 216.5L178.3 219.9L179.2 220.4L179.4 219.4L180.7 219.7L181.6 223.0L185.0 224.2L186.1 221.9L187.0 222.2L187.9 221.2L191.7 224.9L190.4 227.6L190.8 230.0L189.2 230.5L191.6 236.1L190.1 237.5L191.1 240.0L189.7 244.5L191.8 248.7L191.6 254.7L190.4 257.6L207.1 257.7L208.8 259.6L209.6 258.8L211.1 259.7L214.5 259.8L215.4 264.2L212.4 267.2L212.5 271.2L214.4 272.3L215.8 277.6L217.6 279.1L216.4 281.3L215.2 281.4L213.1 283.5L213.0 287.7L209.8 290.8L208.2 296.8L204.5 298.6L203.3 300.5L200.7 299.3L200.0 298.1L198.8 298.3L198.6 297.3L195.9 298.1L194.3 297.1L191.7 298.4L190.0 297.3L186.0 298.3L185.2 296.4L181.8 293.7L180.9 291.4L180.6 292.0L180.3 291.3L179.4 292.0L177.2 291.6L176.6 290.6L174.2 290.2L173.6 289.0L171.6 289.7L169.9 287.3L168.1 287.0L167.3 284.6L165.8 283.4L163.7 284.5L161.6 282.7L156.2 280.5L153.6 282.1L150.9 281.3L150.4 281.9L149.4 280.9L148.6 281.7L148.5 281.0L146.6 280.7L145.1 279.2L145.1 277.2L142.2 276.7L142.0 275.1L141.5 276.2L140.9 275.1L140.2 275.6L140.1 273.9L137.2 273.7L136.3 269.1L134.9 269.8L133.6 269.1L134.4 266.3L132.9 265.7L132.5 263.5L133.2 263.0L132.3 262.7L132.8 261.0L132.1 260.2L133.8 257.6L133.4 255.8L132.0 254.6L132.4 253.0L131.3 251.8L131.1 249.6L133.2 245.5L132.5 241.1L133.3 239.6L132.1 237.4L130.8 236.8L129.1 239.6L127.4 238.5L126.8 239.0L127.1 238.0L126.2 238.6L125.9 237.8L125.5 238.7L124.1 238.1L122.6 239.0L120.0 238.6L115.8 240.4L113.4 240.5L110.0 239.1L110.5 238.1L114.7 236.2L116.3 234.1L116.1 232.6L122.8 232.7L125.5 235.4L128.5 232.7L131.0 233.6L130.8 231.2L131.7 231.5L133.9 230.2L134.8 232.9L136.1 233.0L138.5 230.3L138.9 226.8L140.1 226.1L143.6 226.4L148.0 225.0L148.2 226.1L150.7 225.4L150.7 222.1L152.5 221.7L153.2 219.6L154.1 219.3L153.3 218.0L153.8 215.6L155.0 214.9L156.3 215.5L158.7 209.8Z","x":170.6,"y":256.4,"g":1},
"RR":{"d":"M231.1 64.1L231.1 79.4L213.6 79.4L208.0 91.6L209.4 95.0L206.8 95.8L206.0 97.4L203.9 97.7L202.6 97.2L202.5 95.6L200.1 92.3L195.5 91.5L195.4 92.4L191.7 94.1L189.9 98.4L190.5 100.4L189.4 105.6L190.6 106.3L191.6 108.6L189.1 106.3L185.1 105.7L183.3 101.6L175.7 95.6L176.1 94.3L177.3 95.1L179.0 93.9L178.8 91.7L180.7 88.8L179.8 88.3L179.8 86.3L177.1 82.1L176.7 77.5L175.4 75.5L175.4 72.6L176.5 71.1L176.3 66.3L175.4 66.3L173.8 60.8L171.2 58.3L172.5 56.4L172.8 52.9L170.7 51.6L167.4 51.4L166.1 49.1L162.7 48.7L162.0 44.9L156.7 45.7L152.1 44.0L153.0 39.7L149.3 34.2L149.0 29.5L150.1 27.1L148.5 24.9L145.5 23.5L140.5 17.4L140.4 16.1L142.2 16.2L144.4 18.5L150.4 18.2L153.5 22.2L155.2 20.9L157.8 21.6L157.9 19.9L160.6 22.7L161.6 22.3L161.7 20.5L165.1 20.9L164.8 22.8L166.7 23.4L167.0 25.0L168.9 26.4L170.8 24.3L172.2 25.3L172.4 23.3L171.5 21.8L172.0 19.7L175.0 19.9L175.1 18.4L176.5 17.3L181.3 19.0L183.8 17.3L184.6 18.5L187.0 16.2L190.3 16.2L191.0 13.7L194.4 12.9L193.9 11.7L196.6 12.5L198.9 11.9L200.4 8.8L202.7 8.2L205.1 5.5L203.1 0.8L207.5 1.4L210.9 0.0L212.1 0.4L212.7 2.1L214.2 3.0L214.3 4.8L213.7 9.1L211.7 12.1L217.3 12.8L219.2 14.2L218.4 17.2L220.0 18.1L219.5 19.0L221.6 21.0L220.4 21.9L220.4 23.4L219.1 24.0L219.2 24.9L216.2 26.8L217.2 28.0L216.6 29.3L217.2 30.3L215.6 32.6L214.3 37.9L215.8 46.1L218.4 47.5L218.0 54.1L219.3 53.9L218.9 55.7L221.2 56.2L225.6 61.5L230.7 62.7Z","x":192.8,"y":50.5,"g":1},
"RS":{"d":"M371.6 548.3L367.0 555.3L362.1 566.9L355.4 576.6L348.1 583.8L338.4 590.0L335.5 593.5L336.4 589.7L334.9 587.4L338.9 588.5L339.9 587.6L338.7 587.5L341.8 587.0L344.4 583.8L345.3 584.7L345.4 582.4L346.3 583.2L347.9 582.4L349.3 580.2L349.4 575.8L350.2 576.4L352.2 575.4L352.4 573.2L355.7 571.9L356.5 570.8L356.1 564.8L357.7 565.2L357.7 564.2L358.2 565.3L357.2 566.0L358.4 566.6L359.0 563.3L358.1 562.0L357.2 562.1L356.9 563.7L353.1 564.2L353.0 565.9L351.0 565.1L351.5 563.3L349.3 563.1L348.1 561.9L348.5 561.2L347.5 560.9L348.4 559.6L347.5 560.0L347.4 559.0L347.4 563.8L348.7 563.7L349.2 565.1L350.5 564.7L349.6 566.9L349.9 565.9L348.9 565.4L347.4 567.8L347.6 571.8L346.9 571.3L347.4 571.2L347.1 569.2L346.2 569.1L346.3 572.8L344.3 573.7L345.2 576.2L342.5 577.0L342.5 579.0L337.7 579.9L336.2 585.8L333.2 586.7L332.8 588.7L334.9 589.9L333.3 590.0L332.8 591.5L333.5 591.3L333.3 592.0L335.1 591.0L333.5 591.0L334.7 590.0L335.6 591.4L334.2 592.4L335.3 591.6L335.3 593.6L334.2 593.7L331.9 596.4L327.0 608.3L324.4 611.3L315.2 618.4L313.1 616.8L313.5 614.9L314.3 615.6L314.7 614.4L314.1 610.6L314.8 608.7L316.4 607.4L317.3 608.2L318.9 606.0L319.4 603.2L320.2 603.8L321.5 602.4L321.5 603.6L323.4 605.1L325.1 604.3L327.6 599.0L326.0 595.7L327.1 592.9L324.8 593.9L325.5 594.0L324.1 596.0L325.6 596.7L322.1 598.3L320.0 601.1L315.4 600.0L311.4 596.7L309.9 591.9L306.5 589.4L304.5 589.5L299.1 585.2L297.0 582.0L293.2 581.8L290.6 579.0L289.6 580.1L287.0 578.9L285.3 575.4L281.9 572.2L280.6 572.6L280.5 574.1L279.6 573.9L277.2 576.0L275.2 576.1L275.0 571.4L263.1 560.6L259.0 560.3L256.8 563.6L252.1 563.5L251.5 562.3L250.2 562.0L253.0 560.6L254.9 558.8L255.6 556.3L258.2 555.2L263.2 550.5L266.4 545.0L269.0 544.3L270.9 539.9L272.5 539.5L275.4 536.7L275.2 535.3L277.1 534.9L277.3 533.0L280.0 534.0L280.5 532.7L278.9 531.1L281.4 529.1L282.2 529.9L283.1 528.5L283.9 528.8L284.8 527.0L287.7 525.0L288.7 525.6L289.1 524.8L290.2 525.0L289.5 523.8L291.7 523.7L293.5 519.9L294.1 520.7L295.6 520.5L295.7 519.4L296.3 520.1L297.0 518.6L297.9 519.4L299.7 517.8L300.7 518.8L300.8 517.8L301.7 518.5L303.4 515.4L304.8 516.1L307.9 513.4L310.0 514.5L311.1 514.0L311.5 514.9L312.7 514.2L313.8 514.6L313.6 513.5L314.9 513.7L315.6 512.8L316.8 513.5L316.6 514.9L318.9 513.6L320.2 513.9L320.9 512.7L321.7 514.9L321.9 514.0L322.5 514.6L323.5 514.1L326.0 515.9L326.4 515.2L329.2 515.6L329.9 514.8L330.9 516.2L332.0 516.5L332.0 515.5L332.7 515.5L332.4 516.1L333.4 516.7L334.1 515.7L335.1 516.9L337.4 517.5L336.5 517.8L338.3 519.7L338.7 519.0L339.7 519.8L339.7 519.2L342.3 519.2L343.2 520.7L344.4 520.3L345.3 521.8L346.1 521.3L348.2 523.7L350.6 524.6L357.7 533.5L361.8 533.9L361.9 534.6L363.4 534.1L364.8 535.1L365.3 534.1L365.7 535.0L367.7 534.2L368.1 534.9L369.5 534.3L369.7 535.1L370.8 534.5L371.9 537.2L370.5 537.0L369.1 539.1L368.2 538.8L367.7 541.9L368.5 542.8L367.1 544.2L368.2 544.0L367.8 545.0L364.6 547.1L366.7 548.7L365.5 547.2L367.9 546.3Z","x":316.5,"y":554.3,"g":1},
"SC":{"d":"M388.2 495.1L388.7 495.2L389.0 498.2L390.3 499.0L387.3 506.5L387.6 507.6L388.9 508.0L388.0 509.9L388.3 511.4L389.2 511.5L388.6 513.4L390.7 513.7L390.4 514.8L389.7 514.3L388.5 515.0L388.7 516.4L389.8 516.7L389.4 518.3L388.5 518.2L391.5 517.5L392.3 518.5L390.2 522.7L390.4 523.8L388.6 525.1L388.7 527.7L386.3 535.8L378.1 541.3L371.6 548.3L367.9 546.3L365.5 547.2L366.7 548.7L364.6 547.1L367.8 545.0L368.2 544.0L367.1 544.2L368.5 542.8L367.7 541.9L368.2 538.8L369.1 539.1L370.5 537.0L371.9 537.2L370.8 534.5L369.7 535.1L369.5 534.3L368.1 534.9L367.7 534.2L365.7 535.0L365.3 534.1L364.8 535.1L363.4 534.1L361.9 534.6L361.8 533.9L357.7 533.5L350.6 524.6L348.2 523.7L346.1 521.3L345.3 521.8L344.4 520.3L343.2 520.7L342.3 519.2L339.7 519.2L339.7 519.8L338.7 519.0L338.3 519.7L336.5 517.8L337.4 517.5L335.1 516.9L334.1 515.7L333.4 516.7L332.4 516.1L332.7 515.5L332.0 515.5L332.0 516.5L330.9 516.2L329.9 514.8L329.2 515.6L326.4 515.2L326.0 515.9L323.5 514.1L322.5 514.6L321.9 514.0L321.7 514.9L320.9 512.7L320.2 513.9L318.9 513.6L316.6 514.9L316.8 513.5L315.6 512.8L314.9 513.7L313.6 513.5L313.8 514.6L308.6 514.1L309.4 513.0L309.0 512.1L309.7 512.4L309.3 511.8L311.0 510.5L309.7 505.7L311.5 499.6L313.7 500.4L317.0 499.5L319.9 501.8L325.3 501.0L327.4 502.1L333.8 502.6L338.5 505.1L344.3 504.8L345.6 506.9L345.9 506.1L348.4 505.3L347.3 502.2L348.2 500.7L350.7 499.2L352.8 500.0L352.9 499.3L353.4 500.2L353.9 499.4L356.2 499.4L357.6 496.6L358.8 496.4L358.5 495.6L362.3 497.7L362.2 496.9L363.4 496.0L364.5 496.8L364.6 496.0L365.4 496.6L367.6 495.8L367.7 496.5L368.1 495.7L374.0 499.3L379.9 495.6Z","x":360.1,"y":515.4,"g":1},
"SE":{"d":"M575.5 249.9L568.5 253.8L561.2 264.9L558.3 266.6L556.3 266.1L555.9 266.9L553.8 266.0L552.9 264.6L551.3 264.1L550.7 262.4L551.3 261.0L550.0 260.6L549.4 258.1L547.5 256.5L547.2 255.1L547.9 253.3L550.2 253.0L551.1 254.0L553.8 253.0L553.1 248.8L555.0 247.3L553.5 242.0L551.0 240.7L551.4 239.1L550.3 237.8L551.1 236.4L550.3 235.7L550.6 234.7L552.5 234.7L554.2 236.3L555.3 236.2L557.3 237.8L559.8 238.3L562.6 240.4L566.3 241.6L567.5 244.1L570.5 246.2L572.0 246.1L572.9 248.7L574.5 248.5Z","x":559.5,"y":251.4,"g":0},
"SP":{"d":"M360.0 397.0L361.9 398.4L365.7 398.5L367.0 399.3L374.1 399.0L375.7 400.2L378.5 399.9L377.8 402.1L378.8 405.0L379.9 405.4L381.5 402.9L382.5 403.0L383.0 406.7L384.1 407.5L385.2 403.1L391.6 402.3L393.9 402.8L394.2 400.9L396.2 402.8L397.5 402.5L398.2 401.1L399.4 402.5L400.1 400.3L402.3 400.2L403.4 401.3L406.0 399.9L406.4 401.3L409.2 403.1L408.7 407.6L410.8 408.7L411.7 410.7L409.5 414.5L410.9 415.9L413.0 423.0L416.8 422.0L417.7 422.8L418.3 422.1L419.0 423.3L420.7 423.8L420.5 426.0L418.9 427.0L417.9 429.6L419.1 432.3L417.4 433.4L419.3 434.3L418.1 435.0L417.4 437.0L418.3 438.8L422.2 440.7L422.4 442.7L421.1 443.3L423.3 444.2L423.0 446.5L426.2 445.8L426.3 446.8L429.8 445.1L430.2 446.1L431.7 445.8L432.8 444.2L431.3 443.4L433.1 442.5L432.8 441.3L433.7 441.4L433.6 442.5L434.9 441.7L435.5 442.5L436.5 441.5L437.7 442.5L441.6 440.2L446.7 438.6L447.8 439.1L449.3 441.8L451.3 442.3L453.2 441.3L455.9 441.9L456.6 442.9L454.9 445.4L451.9 446.2L451.5 445.6L446.9 447.7L446.5 450.5L445.5 451.6L448.0 453.9L446.2 454.2L445.2 453.3L442.9 454.7L443.5 455.4L442.5 456.3L441.2 455.9L440.6 457.3L437.5 457.9L437.3 461.2L429.6 460.2L426.6 461.3L425.7 463.7L424.1 464.6L422.7 463.4L422.3 464.4L415.9 467.1L413.2 469.0L413.0 470.4L401.0 478.6L399.2 480.6L399.2 482.4L396.4 484.6L397.4 483.2L396.1 483.7L395.0 483.0L395.5 482.0L394.0 479.4L392.9 480.6L391.6 479.4L390.0 481.2L388.9 480.5L389.2 477.1L390.2 475.6L388.9 474.5L383.2 474.7L382.5 473.9L381.6 474.8L377.9 474.6L377.7 472.7L379.5 469.3L378.2 468.8L377.1 467.3L377.4 466.1L373.2 461.5L374.1 459.2L373.1 458.2L372.9 456.1L373.9 454.8L373.0 454.3L372.2 450.6L368.6 448.8L367.4 446.4L363.6 447.3L357.1 446.4L356.0 447.5L354.9 447.2L353.6 444.8L349.6 444.1L347.9 442.8L340.9 442.8L340.7 442.1L338.6 442.2L334.9 440.4L333.2 442.9L332.8 441.9L328.9 442.2L327.7 441.2L325.9 442.1L322.5 441.1L320.0 442.6L319.7 442.0L321.3 440.0L329.1 435.6L330.8 433.9L332.0 431.3L335.8 427.0L335.2 424.9L337.1 424.3L338.7 421.9L338.5 418.5L342.4 415.4L342.8 410.7L346.6 406.2L350.1 405.0L352.4 401.0ZM438.7 459.5L440.2 460.3L440.3 461.3L439.3 461.8L440.3 462.3L439.9 463.3L439.3 462.5L436.7 462.5Z","x":386.7,"y":436.4,"g":1},
"TO":{"d":"M392.4 165.5L395.1 166.9L399.6 166.9L400.2 168.7L404.7 170.2L405.8 171.7L407.4 182.9L405.1 194.1L403.3 196.7L401.7 197.5L403.2 199.3L404.8 198.7L405.7 199.5L404.1 201.4L405.4 201.4L412.5 211.2L419.1 208.7L421.2 210.5L420.7 214.6L416.1 216.6L414.5 219.6L414.5 223.8L413.3 224.3L412.1 227.2L414.3 227.2L416.8 232.7L418.3 232.4L419.9 233.8L420.2 235.0L418.6 237.8L420.7 238.9L421.5 242.8L422.8 244.7L425.9 245.4L428.0 244.8L428.4 246.2L431.6 246.3L433.1 244.7L433.1 246.2L432.4 247.5L431.1 247.8L431.1 249.0L425.2 252.3L425.5 253.4L424.6 254.0L424.1 256.4L422.4 257.8L421.2 260.9L419.0 262.4L421.1 266.0L427.1 267.7L423.7 267.8L422.7 271.6L425.8 272.1L422.4 274.3L423.1 279.1L424.6 281.5L426.1 281.4L424.2 283.0L423.8 288.8L426.7 288.3L422.9 289.4L422.9 287.4L422.1 286.8L421.5 289.1L417.0 289.1L413.5 291.6L409.7 292.6L408.8 293.8L407.4 293.2L406.6 294.1L405.8 292.5L404.5 292.5L404.6 291.5L403.4 291.2L402.9 292.9L403.6 295.4L402.8 297.0L400.5 294.5L398.4 294.5L396.9 293.3L395.3 294.4L395.6 292.0L391.1 294.2L390.1 291.6L388.9 294.6L388.6 290.5L386.7 289.4L384.7 286.5L382.9 288.9L381.4 288.0L380.7 286.2L378.9 287.7L377.1 291.2L376.9 293.9L368.6 289.0L362.8 287.0L362.6 284.5L364.3 282.6L363.9 281.5L365.1 280.0L361.6 282.4L359.4 287.3L357.7 286.7L357.3 284.0L356.4 283.4L357.8 280.5L356.7 276.9L357.5 271.9L356.2 269.6L357.2 267.3L356.3 267.0L355.9 265.2L357.9 258.9L358.0 252.5L360.8 247.7L361.2 244.1L365.6 235.6L366.7 230.8L371.1 224.7L373.5 223.6L378.3 216.1L380.3 207.3L377.5 205.2L376.7 203.1L379.7 198.2L379.3 193.3L382.3 191.1L387.4 189.4L388.0 186.7L390.0 184.3L392.0 184.6L391.2 181.4L393.2 180.5L392.7 178.7L394.3 177.5L393.4 174.7L395.2 174.0L395.8 172.6L393.2 171.1L392.0 169.1L388.7 169.5L386.3 168.4L387.5 167.6L388.6 168.1L389.8 165.8Z","x":392.8,"y":244.3,"g":1}
}}'''

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--teste", action="store_true", help="só testa a conexão com o PNCP")
    ap.add_argument("--site", action="store_true", help="só refaz a página (docs/index.html), sem coletar")
    ap.add_argument("--demo", action="store_true", help="gera um site de teste com dados fictícios em demo/")
    ap.add_argument("--uf", default="", help="um ou mais estados, ex.: BA ou BA,PE")
    ap.add_argument("--regiao", default="", choices=[""] + sorted(REGIOES), help="uma região inteira, ex.: nordeste")
    ap.add_argument("--brasil", action="store_true", help="compatibilidade: o Brasil inteiro já é o padrão")
    a = ap.parse_args()
    ufs = [u.strip().upper() for u in a.uf.split(",") if u.strip()]
    if a.regiao:
        ufs += [u for u in REGIOES[a.regiao] if u not in ufs]
    ufs = ufs or UFS
    invalidas = sorted(set(ufs) - set(UFS))
    if invalidas:
        ap.error("UF(s) inválida(s): " + ", ".join(invalidas))
    if a.demo:
        demo()
        print("Site de demonstração criado na pasta demo/ (dados fictícios; o docs/ não foi alterado).")
        print("Para ver: python3 -m http.server -d demo 8000  e abra http://localhost:8000")
    elif a.teste:
        teste(ufs)
    elif a.site:
        os.makedirs("docs/dados", exist_ok=True)
        caminho = "docs/dados/resumo.json"
        if os.path.exists(caminho):
            with open(caminho, encoding="utf-8") as f:
                resumo = json.load(f)
            for uf, info in resumo["ufs"].items():
                if uf in UFS:
                    completar(info, uf)
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(resumo, f, ensure_ascii=False)
        with open("docs/index.html", "w", encoding="utf-8") as f:
            f.write(pagina())
        print("Página refeita e municípios completados com a lista do IBGE.")
    else:
        achados, falhas, pares = coletar(ufs)
        limite = int(pares * TOLERANCIA)
        if falhas > limite:
            raise SystemExit(f"Coleta incompleta: {falhas} de {pares} consultas falharam (limite aceito: {limite}). Site anterior preservado.")
        gravar(achados, ufs)
        print(f"\nPronto: {len(achados)} editais abertos. Consultas que falharam: {falhas} de {pares} (limite aceito: {limite}).")
        if falhas:
            print("AVISO: parte dos dados pode estar faltando nesta rodada; a próxima atualização tenta de novo.")
        print("Para ver o site: python3 -m http.server -d docs 8000  e abra http://localhost:8000")
