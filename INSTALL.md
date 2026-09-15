# 安装

## Claude Code / 本地 CLI

把整个目录放到技能目录下，目录名必须保持 `product-selection`：

```bash
# 项目级
mkdir -p .claude/skills
git clone https://github.com/lingling1989r/AuraBaba_ProductSelection_Skill.git \
  .claude/skills/product-selection

# 或个人级
git clone https://github.com/lingling1989r/AuraBaba_ProductSelection_Skill.git \
  ~/.claude/skills/product-selection
```

放好后重启会话，`product-selection` 会在可用技能列表里出现。

### 配置密钥

```bash
cd .claude/skills/product-selection
cp config.local.example.json config.local.json
# 然后编辑 config.local.json，把三个数据源的 key 换成你自己的
```

`config.local.json` 已在 `.gitignore` 里，不会被提交。

### 依赖

只需要 `python3` + `openpyxl`（写 Excel）。图表是手写内联 SVG，不依赖任何 JS 库。

```bash
pip3 install openpyxl
python3 scripts/mcp_call.py check        # 自检数据源连通性
```

也可以用环境变量 `SKS_CONFIG` 指向另一份配置文件，而不改动仓库里的这份。

## AuraBaba 工作区

这个技能已经发布在工作区里，直接在会话里用即可，不需要再装一遍：

```bash
aura skill list | grep product-selection
```
