# 閰嶇疆涓庣洰褰曠粨鏋?

启动入口：`rdx.bat` 会调用 `rdx_launcher.py`，启动前会先做环境自检（Python 依赖 / uv / ngrok），缺失时会给出安装建议与命令。
鏈〉鑱氱劍锛?*濡備綍鎶?RDX-MCP 璺戠ǔ**锛堣矾寰勩€佺洰褰曘€佹棩蹇椼€佺綉缁滀紶杈撳畨鍏級锛屼互鍙婂父瑙侀厤缃」鍦ㄥ綋鍓嶅疄鐜颁腑鈥滃埌搴曞湪鍝噷琚鍙栤€濄€?
## 鐜鍙橀噺涓€瑙堬紙鎸夊綋鍓嶅疄鐜帮級

> **绾﹀畾**锛氫笅琛ㄤ互 `extensions/rdx-mcp/rdx/server.py`锛坰erver 鐢熷懡鍛ㄦ湡锛変笌 `extensions/rdx-mcp/rdx_launcher.py`锛堝惎鍔ㄥ叆鍙ｏ級涓哄噯锛沗extensions/rdx-mcp/rdx/config.py` 浼氳鍙栦竴缁勭幆澧冨彉閲忓苟鐢熸垚 `RdxConfig`锛屼絾骞堕潪鎵€鏈夊瓧娈甸兘浼氬湪 server 涓娑堣垂銆?
| 鍙橀噺 | 浣滅敤 | 榛樿鍊硷紙浠ｇ爜锛?| 璇诲彇浣嶇疆锛堜唬鐮侊級 |
|---|---|---|---|
| `RDX_RENDERDOC_PATH` | 灏?RenderDoc Python module 鐩綍鍔犲叆 `sys.path`锛堣В鍐?`import renderdoc`锛?| 鏃?| `extensions/rdx-mcp/rdx_launcher.py`銆乣extensions/rdx-mcp/rdx/config.py` |
| `RDX_LOG_LEVEL` | 鏃ュ織绾у埆 | `INFO` | `extensions/rdx-mcp/rdx_launcher.py`銆乣extensions/rdx-mcp/rdx/server.py`銆乣extensions/rdx-mcp/rdx/config.py` |
| `RDX_SSE_HOST` / `RDX_SSE_PORT` | SSE 鐩戝惉鍦板潃 | `127.0.0.1` / `8765` | `extensions/rdx-mcp/rdx_launcher.py`銆乣extensions/rdx-mcp/rdx/server.py` |
| `RDX_ARTIFACT_DIR` | artifact 瀛樺偍鏍圭洰褰曪紙CAS 鐩綍锛?| `./rdx_artifacts` | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_ALLOWED_HOSTS` / `RDX_ALLOWED_ORIGINS` | 鍏佽鐨?Host/Origin锛堢敤浜庡叕缃戣浆鍙戞椂鏀捐锛?| 绌猴紙涓嶉檺鍒讹級 | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_ARTIFACT_STORE` | artifact 鐩綍锛堣繘鍏?`RdxConfig`锛?| `./rdx_artifacts` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_DATA_DIR` | `RdxConfig` 鏁版嵁鐩綍锛堝綋鍓?server 鏈洿鎺ユ秷璐癸級 | `./rdx_data` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_REPORT_DIR` | `RdxConfig` report 杈撳嚭鐩綍锛堝綋鍓?server 鏈洿鎺ユ秷璐癸級 | `./rdx_reports` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_GPU_VENDOR` | GPU vendor 鍋忓ソ锛堣繘鍏?`RdxConfig`锛?| `any` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_SPIRV_TOOLS_PATH` | SPIRV-Tools 璺緞锛堣繘鍏?`RdxConfig`锛?| 绌?| `extensions/rdx-mcp/rdx/config.py` |
| `RDX_HEADLESS` | 寮哄埗 headless锛堣繘鍏?`RdxConfig`锛?| `true` | `extensions/rdx-mcp/rdx/config.py` |

### 閲嶈宸紓锛歚RDX_ARTIFACT_DIR` vs `RDX_ARTIFACT_STORE`

- **artifact store 鐨勫疄闄呮牴鐩綍**锛氬綋鍓?server 鍦ㄥ惎鍔ㄦ椂浣跨敤 `RDX_ARTIFACT_DIR` 鍒濆鍖?`ArtifactStore`锛堣 `extensions/rdx-mcp/rdx/server.py`锛夈€?- **`RDX_ARTIFACT_STORE`**锛氫細鍐欏叆 `RdxConfig.artifact.store_path`锛屼絾鐩墠 server 鏈皢璇ュ瓧娈电敤浜庡垵濮嬪寲 `ArtifactStore`銆?
濡傛灉浣犲彧鎯斥€滆窇璧锋潵涓旀墍鏈?artifacts 閮借兘钀界洏鈥濓紝寤鸿**浼樺厛璁剧疆 `RDX_ARTIFACT_DIR`**銆?
## 鐩綍缁撴瀯涓庝骇鐗?
### Artifact Store锛圕AS锛?
`ArtifactStore` 浣跨敤 SHA256 鍋氬唴瀹瑰鍧€锛岃惤鐩樺竷灞€绫讳技 git object storage锛堣 `extensions/rdx-mcp/rdx/utils/artifact_store.py`锛夛細

```
<RDX_ARTIFACT_DIR>/
  <sha[:2]>/
    <sha[2:4]>/
      <sha256>
```

宸ュ叿杩斿洖鐨?`ArtifactRef.uri` 浣跨敤 `rdx://` scheme锛屼緥濡傦細

```
rdx://artifacts/ab/cd/abcdef0123...
```

### 瀵煎嚭鏂囦欢锛坄rd.export.*` / 閮ㄥ垎 `rd.macro.*`锛?
- 澶у鏁板鍑虹被宸ュ叿瑕佹眰鏄惧紡浼犲叆 `output_path` / `output_dir`锛屽苟鍦?*杩愯 RDX-MCP 鐨勬満鍣?*涓婂啓鏂囦欢銆?- 寤鸿鍦?Windows 涓嬩娇鐢ㄧ粷瀵硅矾寰勶紝鎴栫‘淇濈浉瀵硅矾寰勭殑宸ヤ綔鐩綍鍙啓銆?



