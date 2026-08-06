# 星衍AI放射质控 · 后端包
# 显式声明为包，确保 PyInstaller 在冻结（打包 exe）时能将 server.main / server.db /
# server.models / server.license_web 作为正规包收集，避免「无 __init__ 的命名空间包
# 被漏打包」导致 exe 双击静默崩溃（console=False 下无任何报错）。
