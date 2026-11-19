#!/system/bin/sh
# Generated from src/container-profiles.json; do not edit by hand.
ancli_profile_name_zh_minimal() { printf '%s\n' '最小运行环境'; }
ancli_profile_name_en_minimal() { printf '%s\n' 'Minimal runtime'; }
ancli_profile_description_zh_minimal() { printf '%s\n' 'Python、pip、Node.js、npm、Git、curl 与证书，用于 Agent 基础安装。'; }
ancli_profile_description_en_minimal() { printf '%s\n' 'Python, pip, Node.js, npm, Git, curl and certificates for agent installation.'; }
ancli_profile_packages_minimal() { printf '%s\n' 'ca-certificates curl python3 python3-pip git nodejs npm'; }
ancli_profile_name_zh_standard() { printf '%s\n' '标准 Agent 环境'; }
ancli_profile_name_en_standard() { printf '%s\n' 'Standard Agent'; }
ancli_profile_description_zh_standard() { printf '%s\n' '最小环境加 ZIP 与 unzip 归档工具。'; }
ancli_profile_description_en_standard() { printf '%s\n' 'Minimal environment plus ZIP archive tools.'; }
ancli_profile_packages_standard() { printf '%s\n' 'ca-certificates curl python3 python3-pip git nodejs npm zip unzip'; }
ancli_profile_name_zh_developer() { printf '%s\n' '开发工具环境'; }
ancli_profile_name_en_developer() { printf '%s\n' 'Developer tools'; }
ancli_profile_description_zh_developer() { printf '%s\n' '标准环境加编译工具、binutils、Java 17 和 ADB 客户端。'; }
ancli_profile_description_en_developer() { printf '%s\n' 'Standard environment plus build tools, binutils, Java 17 and the ADB client.'; }
ancli_profile_packages_developer() { printf '%s\n' 'ca-certificates curl python3 python3-pip git nodejs npm zip unzip build-essential binutils openjdk-17-jre-headless adb'; }
ancli_profile_name_zh_full() { printf '%s\n' '完整开发环境'; }
ancli_profile_name_en_full() { printf '%s\n' 'Full development'; }
ancli_profile_description_zh_full() { printf '%s\n' '开发环境加 JSON、搜索、终端、网络诊断及中文输入组件。'; }
ancli_profile_description_en_full() { printf '%s\n' 'Developer environment plus JSON, search, terminal, network diagnostics and Chinese input tools.'; }
ancli_profile_packages_full() { printf '%s\n' 'ca-certificates curl python3 python3-pip git nodejs npm zip unzip build-essential binutils openjdk-17-jre-headless adb jq ripgrep less file procps dnsutils openssh-client tmux vim-tiny fcitx5 fcitx5-chinese-addons'; }
ancli_profile_name_zh() { case "$1" in
    minimal) ancli_profile_name_zh_minimal ;;
    standard) ancli_profile_name_zh_standard ;;
    developer) ancli_profile_name_zh_developer ;;
    full) ancli_profile_name_zh_full ;;
    *) return 1 ;;
  esac
}
ancli_profile_name_en() { case "$1" in
    minimal) ancli_profile_name_en_minimal ;;
    standard) ancli_profile_name_en_standard ;;
    developer) ancli_profile_name_en_developer ;;
    full) ancli_profile_name_en_full ;;
    *) return 1 ;;
  esac
}
ancli_profile_description_zh() { case "$1" in
    minimal) ancli_profile_description_zh_minimal ;;
    standard) ancli_profile_description_zh_standard ;;
    developer) ancli_profile_description_zh_developer ;;
    full) ancli_profile_description_zh_full ;;
    *) return 1 ;;
  esac
}
ancli_profile_description_en() { case "$1" in
    minimal) ancli_profile_description_en_minimal ;;
    standard) ancli_profile_description_en_standard ;;
    developer) ancli_profile_description_en_developer ;;
    full) ancli_profile_description_en_full ;;
    *) return 1 ;;
  esac
}
ancli_profile_packages() { case "$1" in
    minimal) ancli_profile_packages_minimal ;;
    standard) ancli_profile_packages_standard ;;
    developer) ancli_profile_packages_developer ;;
    full) ancli_profile_packages_full ;;
    *) return 1 ;;
  esac
}
ancli_profile_display_packages() {
  _profile_display_packages=$(ancli_profile_packages "$1") || return 1
  if [ "${2:-zh}" = en ]; then printf '%s\n' "$_profile_display_packages" | sed 's/ /, /g';
  else printf '%s\n' "$_profile_display_packages" | sed 's/ /、/g'; fi
}
