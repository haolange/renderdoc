# 蹇€熷紑濮嬶紙Quickstart锛?

启动入口：`rdx.bat` 会调用 `rdx_launcher.py`，启动前会先做环境自检（Python 依赖 / uv / ngrok），缺失时会给出安装建议与命令。
鏈〉鐩爣锛?*鏈€鐭矾寰勮窇璧锋潵**锛屽苟鑳藉湪 MCP 瀹㈡埛绔腑鎸夋楠よ皟鐢?`rd.*` 宸ュ叿瀹屾垚涓€娆″熀鏈殑 capture 鎵撳紑涓庢祻瑙堛€?
## 鍓嶇疆鏉′欢

**蹇呴渶**

- Python `>= 3.10`锛堣 `extensions/rdx-mcp/pyproject.toml`锛夈€?- 闇€瑕佹湰浠撳簱鐨?RenderDoc 婧愮爜骞跺畬鎴愭湰鍦扮紪璇戯紝鐢熸垚 `renderdoc.pyd` 涓?`renderdoc.dll`锛圵indows 榛樿杈撳嚭瑙佷笅锛夈€?- RenderDoc 鐨?Python module 鍙瀵煎叆锛歚import renderdoc`銆?  - 甯歌鍋氭硶锛氳缃?`RDX_RENDERDOC_PATH`锛屽皢 RenderDoc 鐨?Python module 鎵€鍦ㄧ洰褰曞姞鍏?`sys.path`锛坄extensions/rdx-mcp/rdx_launcher.py` 浼氳鍙栧畠锛夈€?  - Windows 涓?`rdx.bat` 浼氳嚜鍔ㄦ帰娴嬮粯璁よ緭鍑哄竷灞€锛堜緥濡?`x64\Development\pymodules`锛夛紝鏈懡涓椂鍐嶆墜鍔ㄨ缃嵆鍙€?
**鍙€夛紙鎸夐渶锛?*

- 鑻ヤ綘瑕佸仛 shader 鐑慨澶?楠岃瘉锛堝 `rd.shader.edit_and_replace`銆乣rd.macro.shader_hotfix_validate`锛夛細鍏跺彲鐢ㄦ€у彇鍐充簬 capture 鐨?API銆乻hader 缂栫爜浠ュ強 RenderDoc 瀵?`BuildTargetShader` 鐨勬敮鎸侊紙璇﹁ `configuration.md`锛夈€?
## 瀹夎锛堝彲閫夛級

RDX-MCP 鏈川涓婃槸涓€涓?Python 鍖?+ MCP server 鍏ュ彛銆備綘鍙互涓嶅畨瑁咃紝鐩存帴杩愯 `rdx_launcher.py`锛涗篃鍙互鐢?editable 瀹夎寰楀埌 `rdx-mcp` 鍛戒护銆?
```powershell
cd extensions/rdx-mcp
python -m pip install -e .
```

## RenderDoc 婧愮爜鏋勫缓锛堝繀闇€锛學indows 绀轰緥锛?
- 鎵撳紑浠撳簱鏍圭洰褰曠殑 `renderdoc.sln`銆?- 閫夋嫨 `x64` + `Development`锛岀紪璇?`pyrenderdoc_module`锛堜細鑱斿姩鐢熸垚 `renderdoc.dll`锛夈€?- 榛樿杈撳嚭锛?  - `x64\Development\pymodules\renderdoc.pyd`
  - `x64\Development\renderdoc.dll`

## 鍚姩鏈嶅姟

### 涓€閿惎鍔紙Windows锛?
鍙屽嚮 `extensions/rdx-mcp/rdx.bat`锛岃剼鏈細鎻愮ず閫夋嫨锛?
- `L`锛圠AN锛夛細榛樿杈撳嚭 SSE 鍐呯綉 URL锛堜緥濡?`http://192.168.x.x:PORT/sse`锛?- `I`锛圛NTERNET锛夛細浼氭彁绀洪€夋嫨 **HTTP**锛堟帹鑽愶紝`https://.../mcp`锛夋垨 **SSE**锛坄https://.../sse`锛夊叕缃?URL

鑴氭湰浼氬仛鍩虹鑷锛圛P 绫诲瀷銆乶grok 瀹夎/鎺堟潈锛夛紝骞舵妸鏈€缁?URL 澶嶅埗鍒板壀璐存澘锛岀洿鎺ョ矘璐村埌瀹㈡埛绔嵆鍙€?濡傞渶璺宠繃鎻愮ず骞跺己鍒?SSE 鎴?HTTP锛屽彲鍦?`rdx.bat` 启动菜单 閲岃缃?`RDX_TRANSPORT=sse` 鎴?`RDX_TRANSPORT=http`銆?棣栨杩愯鏃朵細璇㈤棶榛樿 `.rdc` 鐩綍锛屽苟淇濆瓨鍒?`extensions/rdx-mcp/.rdx_mcp.json`锛堝凡蹇界暐鎻愪氦锛夈€?
ngrok 瀹夎鏂瑰紡锛圵indows锛屼换閫夊叾涓€锛夛細

- `winget install ngrok.ngrok`
- 鎴栨墜鍔ㄤ笅杞?`ngrok.exe` 骞舵斁鍒?`extensions/rdx-mcp/`锛堜笌 `rdx.bat` 鍚岀洰褰曪級鎴栦粨搴撴牴鐩綍

瀹夎鍚庨渶鎵ц涓€娆★細`ngrok config add-authtoken <TOKEN>`锛堝惁鍒?INTERNET 妯″紡浼氳嚜妫€澶辫触锛夈€?
濡傛灉鏈厤缃?authtoken锛岃剼鏈細鎻愮ず浣犵矘璐村苟灏嗗叾淇濆瓨鍒?`extensions/rdx-mcp/.rdx_mcp.json`锛堝凡鍔犲叆 `.gitignore`锛岄伩鍏嶆剰澶栨彁浜わ級銆?
### 鏂瑰紡 A锛歴tdio锛堥粯璁わ紝閫傚悎妗岄潰瀹㈡埛绔?Agent 闆嗘垚锛?
```powershell
python extensions/rdx-mcp/rdx_launcher.py
```

### 鏂瑰紡 B锛歋SE锛堥€傚悎 Web client锛?
```powershell
python extensions/rdx-mcp/rdx_launcher.py --transport sse --host 127.0.0.1 --port 8765
```

> **璇存槑**锛歋SE 鐩戝惉鍦板潃鏈€缁堢敱 `RDX_SSE_HOST` / `RDX_SSE_PORT` 鍐冲畾锛沗rdx_launcher.py` 浼氭妸鍛戒护琛屽弬鏁板啓鍥炵幆澧冨彉閲忓悗鍐嶅惎鍔紙瑙?`extensions/rdx-mcp/rdx_launcher.py`銆乣extensions/rdx-mcp/rdx/server.py`锛夈€?
### 鏂瑰紡 C锛氫娇鐢?`rdx-mcp` 鍏ュ彛锛堝畨瑁呭悗锛?
```powershell
rdx-mcp
```

## MCP 瀹㈡埛绔渶灏忛厤缃紙绀轰緥锛?
涓嶅悓瀹㈡埛绔殑閰嶇疆鏂囦欢鏍煎紡涓嶅畬鍏ㄤ竴鑷达紝浣嗘牳蹇冮兘鏄€滃惎鍔ㄤ竴涓?stdio MCP server 鐨勫懡浠よ鈥濄€備互涓嬫槸涓€涓€氱敤褰㈡€佺殑绀轰緥锛堜粎灞曠ず鍏抽敭瀛楁锛夛細

```json
{
  "command": "python",
  "args": ["extensions/rdx-mcp/rdx_launcher.py"],
  "env": {
    "RDX_RENDERDOC_PATH": "D:/path/to/RenderDoc/python",
    "RDX_ARTIFACT_DIR": "D:/rdx/artifacts",
    "RDX_LOG_LEVEL": "INFO"
  }
}
```

## 杩滅▼ Agent 濡備綍鎵撳紑浣犳湰鏈虹殑 .rdc锛?
杩滅▼/浜戠 Agent 璋冪敤 `rd.capture.open_file` 鏃讹紝浼犲叆鐨?`file_path` 浼氬湪 **杩愯 RDX-MCP 鐨勮繖鍙版満鍣?*涓婅鍙栵紝
鎵€浠ュ畠蹇呴』鏄綘鏈満鍙闂殑璺緞锛堜緥濡?`D:\captures\foo.rdc`锛夈€?
寤鸿鍦?MCP 瀹㈡埛绔晶锛堟垨浣犵殑 IDE/鏂囦欢閫夋嫨鍣級鑷閫夋嫨 `.rdc` 璺緞锛屽啀浼犲叆宸ュ叿璋冪敤鍙傛暟銆?
## 绗竴娆¤皟鐢細鏈€灏忓彲鎵ц閾捐矾锛堟墦寮€骞舵祻瑙堜竴甯э級

1. `rd.core.init`
2. `rd.capture.open_file` 鈫?寰楀埌 `capture_file_id`
3. `rd.capture.open_replay` 鈫?寰楀埌 `session_id`
4. `rd.replay.set_frame`锛堥€氬父 `frame_index=0`锛?5. `rd.event.get_action_tree`锛堟祻瑙?action tree/marker锛?6. 鍙€夛細
   - `rd.pipeline.get_state_summary`
   - `rd.resource.list_textures`
   - `rd.export.screenshot`

缁撴潫鍚庡缓璁噴鏀捐祫婧愶細

- `rd.capture.close_replay`
- `rd.capture.close_file`
- `rd.core.shutdown`

鍚庣画寤鸿闃呰锛歚tools.md`锛堝伐鍏峰绾︿笌娓呭崟锛夈€乣workflows.md`锛堟帹鑽愰摼璺笌甯哥敤缁勫悎锛夈€?



