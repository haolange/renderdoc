# 甯歌闂鎺掓煡锛圱roubleshooting锛?

启动入口：`rdx.bat` 会调用 `rdx_launcher.py`，启动前会先做环境自检（Python 依赖 / uv / ngrok），缺失时会给出安装建议与命令。
鏈〉浠モ€滃厛瀹氫綅銆佸啀鏀舵暃鈥濈殑鏂瑰紡鍒楀嚭甯歌鏁呴殰鐐逛笌瀵瑰簲鐨勫鐞嗗缓璁€?
## `ImportError: No module named 'renderdoc'`

**鐜拌薄**

- 鍚姩鍚庡湪璋冪敤浠绘剰闇€瑕?RenderDoc 鐨勫伐鍏凤紙渚嬪 `rd.capture.open_file`銆乣rd.replay.set_frame`銆乣rd.export.screenshot`銆乣rd.debug.pixel_history`锛夋椂鎶ラ敊锛屾垨鏃ュ織鎻愮ず renderdoc module 涓嶅彲鐢ㄣ€?
**鍘熷洜**

- RenderDoc 鐨?Python module 娌℃湁鍦ㄥ綋鍓嶈繘绋嬬殑 `sys.path` 涓紙瑙?`extensions/rdx-mcp/rdx/core/render_service.py` 鐨勯敊璇俊鎭級銆?- RenderDoc 婧愮爜鏈紪璇戝畬鎴愶紝瀵艰嚧 `renderdoc.pyd` 涓嶅瓨鍦ㄦ垨璺緞涓嶅湪榛樿杈撳嚭鐩綍銆?
**澶勭悊**

- 璁剧疆 `RDX_RENDERDOC_PATH` 鎸囧悜 RenderDoc 鐨?Python module 鎵€鍦ㄧ洰褰曪紙`rdx_launcher.py` 浼氭妸瀹冨姞鍏?`sys.path`锛夈€?- 纭璇ョ洰褰曚笅纭疄鑳?`import renderdoc`锛堝彲鍦ㄥ悓鐜涓嬫墜鍔ㄩ獙璇侊級銆?- 鍦?Windows 涓婄紪璇?`renderdoc.sln` 鐨?`pyrenderdoc_module`锛坄x64` + `Development`锛夛紝榛樿杈撳嚭锛?  - `x64\Development\pymodules\renderdoc.pyd`
  - `x64\Development\renderdoc.dll`

## `ImportError: DLL load failed while importing renderdoc: The specified module could not be found.`

**鐜拌薄**

- MCP 鑳藉惎鍔ㄣ€佸鎴风涔熻兘鈥滅湅鍒?tools鈥濓紝浣嗕竴璋冪敤闇€瑕?RenderDoc 鐨勫伐鍏峰氨鎶ヤ笂杩伴敊璇€?
**鍘熷洜锛堝父瑙侊級**

- **缂栬瘧鏃剁敤閿?Python 鐗堟湰**锛歚renderdoc.pyd` 閾炬帴鐨勮繕鏄?`python36.dll`锛屼絾浣犺繍琛?MCP 鐢ㄧ殑鏄?3.10+/3.11+/3.14鈥?- **Windows DLL 鎼滅储璺緞闂锛圥ython 3.8+锛?*锛氬嵆浣跨増鏈尮閰嶏紝`renderdoc.pyd` 渚濊禆鐨?`renderdoc.dll`锛堜互鍙婂悓鐩綍鐨勫叾瀹?DLL锛変篃鍙兘鍥犱负瀹夊叏绛栫暐瀵艰嚧 **PATH 涓嶇敓鏁?*锛岄渶瑕佺敤 `os.add_dll_directory()` 鏄惧紡鍔犲叆 DLL 鐩綍銆?
**澶勭悊**

- 纭 `renderdoc.pyd` 渚濊禆鐨?Python DLL锛堢ず渚嬩細杈撳嚭 `python314.dll` / `python36.dll` 绛夛級锛?  - `py -3 -c "import pathlib,re; b=pathlib.Path(r'x64\\Development\\pymodules\\renderdoc.pyd').read_bytes(); print(sorted(set(m.group(0).decode('ascii','ignore') for m in re.finditer(rb'python\\d{2,3}\\.dll', b, re.I))))"`
- 纭浣犳槸閫氳繃 `extensions/rdx-mcp/rdx_launcher.py` 鍚姩锛堝畠浼氭妸 `RDX_RENDERDOC_PATH` 鍙婂叾鐖剁洰褰曢€氳繃 `os.add_dll_directory()` 鍔犲叆 DLL 鎼滅储璺緞锛夈€?- 鑻ヤ綘鑷鍚姩/宓屽叆 Python锛岃鍦ㄥ鍏ュ墠鏄惧紡鍔?DLL 鐩綍锛?  - `py -3 -c "import os; os.add_dll_directory(r'x64\\Development'); os.add_dll_directory(r'x64\\Development\\pymodules'); import sys; sys.path.insert(0,r'x64\\Development\\pymodules'); import renderdoc"`

## SSE 鐩戝惉涓嶇鍚堥鏈燂紙host/port锛?
**鐜拌薄**

- 浼犱簡 `--host/--port` 浣嗗疄闄呯洃鍚湴鍧€涓嶅锛屾垨绔彛鍐茬獊銆?
**瑕佺偣**

- `extensions/rdx-mcp/rdx_launcher.py` 浼氭妸鍛戒护琛岀殑 `--host/--port` 鍐欏洖鐜鍙橀噺 `RDX_SSE_HOST` / `RDX_SSE_PORT`銆?- `extensions/rdx-mcp/rdx/server.py` 鐨?`main_sse()` 浼氫粠 `RDX_SSE_HOST` / `RDX_SSE_PORT` 璇诲彇鏈€缁堢洃鍚湴鍧€銆?
**澶勭悊**

- 妫€鏌ョ幆澧冨彉閲忔槸鍚﹁鍏朵粬鍚姩鑴氭湰瑕嗙洊銆?- 绔彛鍐茬獊鏃舵洿鎹?`--port` 鎴栫粓姝㈠崰鐢ㄨ繘绋嬨€?
## 杩滅▼瀹㈡埛绔紙Manus 绛夛級鏃犳硶杩炴帴 SSE

**鐜拌薄**

- Manus 鎻愮ず鏃犳硶璁块棶 `http://192.168.x.x:PORT/sse`锛屾垨杩炴帴瓒呮椂 / OAuth 澶辫触銆?
**鍘熷洜**

- `192.168.* / 10.* / 172.16.*` 灞炰簬鍐呯綉鍦板潃锛岃繙绋嬫矙绠辨棤娉曠洿鎺ヨ闂綘鐨勫眬鍩熺綉銆?
**澶勭悊**

- 浣跨敤 `rdx.bat` 鐨?**INTERNET** 妯″紡锛岃嚜鍔ㄥ惎鐢?ngrok 骞惰幏寰楀叕缃?URL銆?- 纭繚宸叉墽琛岋細`ngrok config add-authtoken <TOKEN>`锛屼笖 `ngrok` 鍦?PATH 涓€?- 杩炴帴鏃朵娇鐢?`rdx.bat` 杈撳嚭骞跺鍒剁殑 URL锛堝舰濡?`https://xxxx.ngrok-free.app/sse`锛夈€?
**琛ュ厖**

- INTERNET 妯″紡鏀寔 **HTTP/streamable**锛坄/mcp`锛夋垨 **SSE**锛坄/sse`锛夈€侶TTP 鏇寸ǔ瀹氾紱涓€閿繍琛屼細鎻愮ず閫夋嫨銆侻anus 涓閫夋嫨 **HTTP** 骞剁矘璐?`https://.../mcp`銆?
### 鎶ラ敊 `HTTP 421` / `Invalid Host header`

**鐜拌薄**

- 鏃ュ織鍑虹幇 `Invalid Host header: <ngrok鍩熷悕>`锛屽苟杩斿洖 `HTTP 421`銆?
**鍘熷洜**

- MCP 鐨?DNS rebinding 淇濇姢鎷掔粷浜?ngrok 鍩熷悕鐨?Host 澶淬€?
**澶勭悊**

- 閲嶆柊杩愯 `rdx.bat` 鐨?**INTERNET** 妯″紡锛堣剼鏈細鑷姩娉ㄥ叆 `RDX_ALLOWED_HOSTS`锛夈€?
## 鐘舵€佸彉鍖栫偣瀹氫綅缁撴灉涓嶇ǔ瀹氾紙`rd.macro.find_state_change_point`锛?
**甯歌鍘熷洜**

- capture 鍐呴儴瀛樺湪闈炵‘瀹氭€э紙渚嬪渚濊禆鏈垵濮嬪寲鍐呭瓨銆侀殢鏈洪噰鏍枫€佹椂闂寸浉鍏宠緭鍏ワ級锛屽鑷粹€滃悓涓€ event 鐨勮緭鍑?鐘舵€佲€濆湪涓嶅悓杩愯闂翠笉涓€鑷淬€?- `state_path/target_value` 閫夋嫨涓嶅绋冲畾锛堜緥濡傚紩鐢ㄤ細鍙樺寲鐨勫姩鎬佹暟缁勯」銆佹垨鐘舵€佸湪澶氫釜鐩搁偦 event 鍐呮潵鍥炲垏鎹級銆?
**澶勭悊寤鸿**

- 鍏堢敤 `rd.event.search_actions` / `rd.event.get_action_tree` 鎶?`event_range` 缂╁皬鍒板彲鐤?marker/pass 鍛ㄥ洿銆?- 瀵瑰€欓€?`event_id` 瀵煎嚭璇佹嵁楠岃瘉绋冲畾鎬э細`rd.export.screenshot`銆乣rd.export.pipeline_state_json`銆乣rd.export.pixel_history_json`锛堝繀瑕佹椂閲嶅杩愯瀵规瘮锛夈€?- 鑻ヤ簩鍒嗙粨鏋滀笉鍙潬锛屾敼鐢?`search_policy='linear'` 鎴栬繘涓€姝ョ缉灏忓尯闂达紝鍐嶇敤 `rd.macro.compare_events_report` 瀵规瘮鍓嶅悗浜嬩欢宸紓銆?
## Shader 鏇挎崲/鐑慨澶嶅け璐ワ紙`rd.shader.edit_and_replace`锛?
**鍏稿瀷鐥囩姸**

- 杩斿洖 `success=false` / `error_message` 鎻愮ず缂栬瘧澶辫触銆乻tage 涓嶆敮鎸併€佹垨鏇挎崲鍚庤緭鍑烘棤鍙樺寲銆?
**鎺掓煡瑕佺偣**

- 鐢?`rd.shader.get_source` / `rd.shader.get_disassembly` 纭鐩爣 shader 鍙鍑猴紱蹇呰鏃跺厛鐢?`rd.shader.extract_binary` 鑾峰彇鍘熷浜岃繘鍒躲€?- 鍏堢敤 `rd.shader.compile` 瀵逛慨鏀瑰悗鐨勪唬鐮佸仛缂栬瘧楠岃瘉锛堝悓涓€ shader 妯″瀷/entry/stage锛夈€?- 鐢ㄦ渶灏忓彉鏇撮獙璇侀摼璺紙鍏堟敼涓€琛?鍔犱竴鏉?guard锛夛紝鍐嶉€愭鍙犲姞澶嶆潅淇敼銆?- 鏇挎崲鍚庣敤 `rd.shader.get_messages` 鏌ョ湅缂栬瘧/鏇挎崲鏃ュ織锛涚敤 `rd.shader.list_replacements` 纭鏇挎崲鏄惁鐢熸晥銆?
**澶勭悊寤鸿**

- 鐢?`rd.macro.shader_hotfix_validate` 鍋氣€滄浛鎹㈠墠/鍚庘€濆姣旓紙鍙粨鍚?`rd.export.screenshot` 淇濆瓨璇佹嵁锛夈€?- 闇€瑕佸洖婊氭椂鐢?`rd.shader.revert_replacement`锛岀‘淇濆悗缁疄楠岀幆澧冨共鍑€銆?
## 鎶ュ憡/璇佹嵁鍖呯敓鎴愬け璐ワ紙`rd.macro.build_bug_report_pack` / `rd.export.repro_bundle_zip`锛?
**鐜拌薄**

- 杈撳嚭璺緞缂烘枃浠躲€亃ip 涓虹┖銆佹垨鎶ユ潈闄?璺緞鐩稿叧閿欒銆?
**鎺掓煡瑕佺偣**

- 纭 `RDX_ARTIFACT_DIR` 涓?`output_dir/output_path` 鎸囧悜鍙啓鐩綍锛圵indows 涓嬪敖閲忛伩鍏嶉渶瑕佺鐞嗗憳鏉冮檺鐨勮矾寰勶級銆?- 纭宸叉垚鍔熸墦寮€ capture 骞惰繘鍏?replay锛堝惁鍒欏鍑虹被宸ュ叿缂哄皯涓婁笅鏂囷級銆?
**澶勭悊**

- 浼樺厛浣跨敤 `rd.macro.build_bug_report_pack` 鐢熸垚涓€浠藉寘鍚?repro bundle + 瑙ｉ噴鏂囨湰鐨勫寘锛涙垨浠呰皟鐢?`rd.export.repro_bundle_zip` / `rd.export.markdown_report` 杈撳嚭鏈€灏忚瘉鎹€?- 鑻ヤ粛澶辫触锛岃褰?`error_message` 骞跺皢瀵煎嚭璺緞涓嬪凡鏈?artifacts锛堝 `rd.export.pipeline_state_json`銆乣rd.export.event_tree_json`锛変竴骞舵彁渚涳紝渚夸簬绂荤嚎鎺掓煡銆?



