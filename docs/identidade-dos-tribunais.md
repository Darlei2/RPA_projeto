# Identidade do tribunal x endereco de acesso

## 1. Diagnostico (antes da v82)

O endereco de login (`base_url`) era, ao mesmo tempo, o endereco de navegacao e a identidade do
tribunal. Mudar o endereco (ou editar o .toml a mao) mudava, sem aviso, o que o robo achava que o
tribunal era:

| Onde | Dependencia do endereco | Efeito de trocar o endereco |
|---|---|---|
| `graus.py` | regex `pje.(trtN).jus.br/(primeirograu\|segundograu)` dava o grupo e o grau | TRT deixava de ter os dois graus juntos e a senha unica; o par podia ser feito pela metade |
| `exigencias.py` | regex `pje.trtN.jus.br` (telefone, RG) e tabela do mapeamento casada pela coluna `url` | linha sem telefone/RG ia para o TRT e parava no meio do formulario |
| `lote.py` | `exigencias.faltando(config.base_url)`, `graus.grupo(config.base_url)`, `OpcaoTribunal.url` nas regras | as duas regras acima, no executar e no assistente |
| `entrega.py` | a planilha de entrega so tinha "Endereco de login" para identificar o tribunal | integracoes que liam a entrega reconheciam o tribunal pelo endereco |
| `lote.listar_tribunais` | arquivo com erro era descartado em silencio | o tribunal "sumia" e o erro aparecia depois, longe da causa |
| `config._assert_official_target` | so exigia `.jus.br` | qualquer host `.jus.br`, inclusive o login unificado com parametros de sessao, era aceito como partida |

Navegacao legitima (continua usando o endereco, e so ela): `flow.open`, `credencial` (abrir o PJe,
link de senha, conferir host do link), `signer` (`site_hosts`), `webapp`, `tribunais.sondar`,
`mapeamento.url_dados_cadastrais`, `browser._local_network_origins`.

## 2. Arquitetura (v82)

```
tribunais/_registro.toml  --registro.py-->  IDENTIDADE (id, nome, ramo, sigla, grau, grupo_credencial)
                                                 |  usada por graus.py, exigencias.py, lote.py, entrega.py
tribunais/<id>.toml  --config.load_config-->  AppConfig (identidade + base_url + seletores + flags)
                                                 |
                          enderecos.py  ------>  SEGURANCA DO ENDERECO (separada da identidade)
                                                    hosts_autorizados, https, sem login unificado,
                                                    sem parametros de sessao, aprovacao para o real
```

- **Fonte unica**: `tribunais\_registro.toml` (um `[[tribunal]]` por tribunal). `id` = nome do
  arquivo, permanente. `endereco_padrao` e `hosts_autorizados` ficam no registro, mas servem so a
  seguranca e ao reconhecimento de registros antigos.
- **Resolucao centralizada** (`config.resolver_identidade`): registro da pasta; ou secao `[tribunal]`
  do proprio .toml (nao herdada); pasta sem registro = identidade pelo nome do arquivo, sem grau nem
  grupo e sem host autorizado. Nunca pelo endereco. Conflitos e tribunais desconhecidos geram
  `ConfigurationError` com a causa.
- **Regras de negocio** recebem o objeto com a identidade (`AppConfig`, `OpcaoTribunal`,
  `registro.Tribunal`). Texto so e aceito por compatibilidade: o id ou, com aviso de obsolescencia,
  o endereco de um registro antigo, comparado com `endereco_padrao` (sem regex).
- **Enderecos** (`enderecos.py`), para ensaio e real: https, host `.jus.br` listado em
  `hosts_autorizados`, sem usuario/porta, nem o login unificado (`sso.cloud.pje.jus.br`), nem
  parametros de sessao/OpenID (`state`, `code`, `nonce`, `redirect_uri`, `session_state`...).
  Envio real: o endereco tem de ser o `endereco_padrao` ou o aprovado pelo operador
  (`python -m pje_rpa enderecos --aprovar <id>`). Mudar o endereco nunca libera o real sozinho.
  Nenhum controle de autenticacao, CAPTCHA, certificado ou 2FA foi alterado: o robo comeca no
  endereco do tribunal e passa por eles pela navegacao normal.
- **Entrega**: colunas novas "ID do tribunal" e "Grau" (no fim, para nao mexer na ordem das colunas);
  texto que comeca com "=" e gravado como texto (nao vira formula no Excel).
- `app.grau` no .toml so e aceito se for igual ao do registro; `app.domain` e ignorado (com aviso).
- **Diagnostico**: `listar_tribunais(pasta, erros)` devolve os erros; `executar` e `mapear` mostram
  "ATENCAO: tribunal ignorado" e enderecos recusados. Comando `python -m pje_rpa enderecos`.

## 3. Migracao

- Nada a migrar nos .toml: todo `base_url` do Git e o `endereco_padrao` do registro, entao os
  tribunais ja liberados continuam liberados. Um .toml editado a mao com outro endereco valido fica
  so em ensaio ate `enderecos --aprovar`. Um endereco invalido (ex.: login unificado com `state`)
  e recusado com a causa; `git checkout -- tribunais\<id>.toml` volta ao oficial.
- Planilhas de entrega antigas (sem "ID do tribunal"): o tribunal e reconhecido pelo endereco
  gravado, comparado com `endereco_padrao` do registro.
- `tribunais\formularios.csv`: ja tinha a coluna `tribunal` (id); linhas sem ela caem no
  `endereco_padrao`.
- Integracoes internas (fora do Git) passam a guardar o mapa por id; o mapa antigo por endereco e
  so lido para migrar e fica intacto para a volta de versao.

## 4. Testes

`tests\test_identidade_tribunal.py` (enderecos ficticios, sem rede):
1. identidade constante quando o endereco muda; 2. duas URLs, mesma identidade; 3. 1G/2G
diferenciados (TJ independente); 4. regras do TRT independentes do endereco (e TJ num host "de TRT"
nao vira TRT); 5. registros antigos reconhecidos e linhas novas com o id; 6. senha unica do grupo
mantida com endereco trocado; 7. registro/configs invalidos recusados e listados; 8. endereco
alterado nao libera o real, aprovacao explicita, enderecos recusados (host, SSO, sessao, usuario,
porta, http); 9. controles de seguranca intactos (producao, homologacao, envio, dados de exemplo,
paradas de CAPTCHA/certificado/bloqueio em todos os tribunais); 10. suite completa.

## 5. Volta de versao

`git revert` do commit da v82 (ou o procedimento do instalador). Os .toml com `endereco_aprovado`
continuam carregando nas versoes anteriores (chave ignorada). A coluna "ID do tribunal" das entregas
novas e ignorada por quem le pelo cabecalho.
