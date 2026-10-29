# Juno

Fale com o computador sem as mãos, no macOS. Você chama o bot pelo nome, dita a mensagem e diz uma palavra para enviar.
Funciona **offline** ([Vosk](https://alphacephei.com/vosk/)): o áudio nunca sai do computador.

```
"juno" → fale a mensagem → "câmbio"   = a mensagem é enviada
"agente vendas"                        = abre o agente "Vendas · OPS" no OpenBot
"agente próximo"                       = abre o próximo agente com mensagens não lidas
```

## Como configurar (5 minutos)

1. **Instale o [FluidVoice](https://github.com/altic-dev/FluidVoice)** (ele transcreve o ditado) e, nos ajustes dele:
   - atalho de ditado: **⌥⌘.** (Option + Command + ponto);
   - *Spoken Send* ligado, com a **mesma palavra de envio** que você vai escolher abaixo (ela é tirada do texto).
2. **Instale o Juno:**
   ```bash
   git clone https://github.com/aryrabelo/juno.git
   cd juno
   ./install.sh
   ```
   O instalador pergunta o **nome do bot** (padrão `juno`) e a **palavra de envio** (padrão `câmbio`).
   Ele só aceita palavras que o modelo de voz em português conhece. Para o “agente próximo” ele compila
   um ajudante em Swift (`swiftc`, vem com `xcode-select --install`).
3. **Teste sem apertar nada:**
   ```bash
   .venv/bin/python juno.py --test --verbose
   ```
   Diga o nome do bot e veja aparecer `ouvi ...`.
4. **Use de verdade:** `.venv/bin/python juno.py`
5. **Permissões do macOS** para o Terminal: Microfone (pedido ao iniciar), Acessibilidade (Ajustes do Sistema ›
   Privacidade e Segurança › Acessibilidade) e Automação/System Events (pedido na primeira tecla).

## Comandos de voz

| Você diz | O que acontece |
|---|---|
| **nome do bot** (ex.: “juno”) | O FluidVoice começa a ditar |
| **palavra de envio** (ex.: “câmbio”), no fim da fala e seguida de silêncio | Para o ditado, espera o texto ser colado e aperta Enter |
| **“agente &lt;apelido&gt;”**, sozinho e fora do ditado | Traz o OpenBot para a frente, abre a busca (⌘K) e abre o agente |
| **“agente próximo”**, sozinho e fora do ditado | Abre o primeiro agente da barra lateral com respostas não lidas (o do numerozinho) |

Os agentes ficam em `agentes.tsv`: uma linha por agente, `apelido<TAB>nome do agente`.
Opção `--cmd-enter`: depois do Enter, aperta também ⌘Enter (para apps que enviam assim).

## Trocar o nome do bot depois

Edite `config.json` e reinicie o Juno. Antes, confira se o modelo conhece a palavra:
```bash
.venv/bin/python juno.py check-words sexta
```

| Campo | Padrão | O que é |
|---|---|---|
| `wake_word` | `juno` | Nome do bot: começa o ditado |
| `send_word` | `câmbio` | Envia a mensagem |
| `agent_word` | `agente` | Prefixo para trocar de agente |
| `agent_app` | `OpenBot` | App onde a troca de agente acontece |
| `next_word` | `próximo` | “agente próximo”: próximo chat com não lidas |

## Ver o que está acontecendo

```bash
.venv/bin/python juno.py stats      # o que foi ouvido, aceito, ignorado (e por quê), tempos
```
Os eventos ficam em `~/Library/Logs/Juno/events.jsonl` (volume, confiança, decisão, tempo de cada tecla).
Só as palavras de comando são gravadas; nada do que você dita.

## Limitações conhecidas
- Fale alto e claro; o comando é confirmado depois de uma pausa (≈1 s).
- A palavra de envio aperta Enter mesmo sem ditado em andamento (na janela em foco).
- Se o FluidVoice não achar um campo de texto em foco, ele não cola e o Juno não aperta Enter.
- O estado do ditado é lido do log do FluidVoice (`~/Library/Logs/Fluid/Fluid.log`).
- Não inicia sozinho no login (ainda).
- “agente próximo” lê o texto “N novas respostas” da barra lateral pela Acessibilidade (OpenBot em português ou inglês).

## Licença
MIT
