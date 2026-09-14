-- Give every default-width Markdown table a bounded share of the text width.
function Table(table_element)
  local columns = #table_element.colspecs
  if columns == 0 then
    return table_element
  end
  for index, specification in ipairs(table_element.colspecs) do
    if specification[2] == nil or specification[2] == 0 then
      table_element.colspecs[index] = { specification[1], 1 / columns }
    end
  end
  return table_element
end

-- Workflow figures.
--
-- The canonical Markdown carries every workflow diagram as a ```mermaid block so
-- it renders directly on GitHub and in the repository. Each block declares a
-- stable identifier on its first line as a mermaid comment:
--
--     %% bdi-figure: client-lifecycle
--     %% bdi-caption: Client lifecycle from original evidence to approved handoff
--
-- For LaTeX/PDF output the block is replaced by the hand-authored TikZ figure at
-- `assets/figures/<identifier>.tex`, which is drawn by the release header's
-- shared `bdi*` styles. Matching on a declared identifier (rather than on the
-- diagram's prose, as an earlier revision did) means a wording change cannot
-- silently drop a figure from the rendered document.
--
-- A mermaid block with no identifier, or one naming a figure file that does not
-- exist, is a release-blocking error rather than a silently emitted wall of
-- mermaid source in a client document.

local function figure_directive(text, key)
  return string.match(text, "%%%%%s*bdi%-" .. key .. ":%s*([^\n]+)")
end

local function trim(value)
  return (string.gsub(value, "^%s*(.-)%s*$", "%1"))
end

function CodeBlock(block)
  if not block.classes:includes("mermaid") then
    return nil
  end
  if FORMAT ~= "latex" then
    return nil
  end

  local identifier = figure_directive(block.text, "figure")
  if identifier == nil then
    error("mermaid block has no '%% bdi-figure: <id>' identifier; " ..
          "every workflow diagram needs one so it can be rendered to PDF")
  end
  identifier = trim(identifier)

  local figure_path = "assets/figures/" .. identifier .. ".tex"
  local handle = io.open(figure_path, "r")
  if handle == nil then
    error("missing TikZ figure '" .. figure_path .. "' for mermaid block '" ..
          identifier .. "'")
  end
  local figure_body = handle:read("*a")
  handle:close()

  local caption = figure_directive(block.text, "caption")
  local latex = "\\begin{figure}[htbp]\n\\centering\n" ..
                "\\resizebox{\\ifdim\\width>\\textwidth\\textwidth\\else\\width\\fi}{!}{%\n" ..
                figure_body ..
                "}\n"
  if caption ~= nil then
    latex = latex .. "\\caption{" .. trim(caption) .. "}\n"
  end
  latex = latex .. "\\end{figure}\n"

  return pandoc.RawBlock("latex", latex)
end

function Code(inline)
  if FORMAT ~= "latex" then
    return nil
  end
  local needs_bounded_code = string.find(inline.text, "_") or string.find(inline.text, "/")
  if not needs_bounded_code then
    return nil
  end
  local value = inline.text
  value = string.gsub(value, "\\", "\\textbackslash{}")
  value = string.gsub(value, "([{}%%#$&])", "\\%1")
  value = string.gsub(value, "_", "\\_\\allowbreak{}")
  value = string.gsub(value, "/", "/\\allowbreak{}")
  value = string.gsub(value, "-", "-\\allowbreak{}")
  return pandoc.RawInline("latex", "\\texttt{" .. value .. "}")
end
