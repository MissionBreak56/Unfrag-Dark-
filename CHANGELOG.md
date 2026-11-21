# Histórico de mudanças

Cada versão tem uma seção `## vX.Y.Z`. O workflow de release usa essa seção como texto da release no GitHub:
**ao subir uma versão nova, escreva aqui o que mudou.**

## v0.4.0
- **Novo:** tempo restante estimado na análise e na desfragmentação (janela e linha de comando); as pausas não contam.
- **Novo:** botão **Pausar / Retomar**; o Parar continua funcionando durante a pausa.
- **Novo:** tarefa agendada **na inicialização do Windows** (`--frequency inicialização`), roda como SYSTEM antes de os programas abrirem arquivos. `pagefile.sys` e a MFT continuam não movíveis pela API.
- **Novo:** a release agora é publicada automaticamente ao subir uma versão nova na `main`, com o texto vindo deste arquivo.
- **Interface:** cartões de estatísticas com a mesma altura e abas com tamanho fixo (trocar de aba não muda mais a altura da janela).

## v0.3.2
- O botão *Procurar atualização* abre a página da última release.

## v0.3.1
- A janela lembra tela cheia, tamanho, posição e monitor ao fechar para a bandeja, sair e reabrir; tolerância ao abrir maximizada.

## v0.3.0
- **Novo:** otimização de SSD (TRIM), com botão *Otimizar SSD*, `unfrag trim` e agendamento; checagem de SSD antes da análise.
- Revisão da leitura da MFT, da linha de comando, do agendador, das configurações e da interface.

## v0.2.0
- **Novo:** tema AMOLED (preto puro); capturas de tela atualizadas.

## v0.1.9
- Cartões de estatísticas com a mesma altura; o cartão *Última execução* deixou de cortar o texto.

## v0.1.8 e anteriores
- Leitura direta da MFT (padrão), início com o Windows, ícone da bandeja por estado e aviso de versão nova.
- Aviso do Windows nunca trava; arquivos que não abrem viram "ignorados", não falhas.
- Cores personalizáveis, blocos livres em cinza, instância única e detalhe do bloco ao clicar no mapa.
- Interface azul com cartões e abas com ícones; bandeja do sistema ao fechar.
- Análise mais rápida (leitura paralela, arquivos triviais, parser de bitmap, GC pausado) e modo Espaço livre mais rápido.
- Opção de threads de leitura; correção do erro "intervalo já livre" (hard links duplicados e mapa desatualizado).
- Ícone original, dicas (tooltips) e explicação de cada modo.
