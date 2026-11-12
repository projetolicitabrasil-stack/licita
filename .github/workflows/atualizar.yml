name: Atualizar editais

on:
  schedule:
    # A cada 2 dias, às 06:00 UTC (03:00 em Brasília, fora do horário comercial)
    - cron: "0 6 */2 * *"
  workflow_dispatch:   # permite rodar manualmente pela aba Actions

permissions:
  actions: write      # só para o passo que mantém o agendamento ativo
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  atualizar:
    runs-on: ubuntu-latest
    timeout-minutes: 345   # limite do GitHub é 360 min por tarefa
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"

      - name: Coletar editais e gerar o site (Brasil inteiro)
        env:
          PYTHONUNBUFFERED: "1"   # mostra o progresso no log em tempo real
        run: python3 atualizar.py

      - uses: actions/configure-pages@v6

      - uses: actions/upload-pages-artifact@v5
        with:
          path: docs

      - id: deploy
        uses: actions/deploy-pages@v5

      # O GitHub desliga agendamentos de repositórios públicos após 60 dias sem atividade.
      # Como este fluxo não faz commits, este passo reativa o agendamento a cada rodada.
      - name: Manter o agendamento ativo
        continue-on-error: true
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh api -X PUT "repos/${{ github.repository }}/actions/workflows/atualizar.yml/enable"
