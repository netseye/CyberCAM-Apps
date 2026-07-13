#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR=$1

# 创建临时文件夹
TMP_ROOT=$SCRIPT_DIR/tmp
if [ -d "$TMP_ROOT" ]
then
    rm -rf "$TMP_ROOT"
fi
mkdir -p "$TMP_ROOT"
mkdir -p "$TMP_ROOT/data"
cp -r ${SCRIPT_DIR}/DEBIAN ${TMP_ROOT}
cp -r ${SCRIPT_DIR}/app ${TMP_ROOT}/data/

# 打包函数：接收打包文件夹路径和目标目录两个参数
pack() {
    local package_dir="$1"
    local output_dir="$2"
    # 如果没提供 output_dir 则使用当前目录
    if [ -z "$output_dir" ]; then
        output_dir="."
    fi
    
    if [ -z "$package_dir" ] || [ -z "$output_dir" ]; then
        echo "错误: 请提供打包文件夹路径和目标目录"
        return 1
    fi
    
    if [ ! -d "$package_dir" ]; then
        echo "错误: 打包文件夹不存在: $package_dir"
        return 1
    fi
    
    # 确保目标目录存在
    mkdir -p "$output_dir"
    
    # 从 DEBIAN/control 文件中提取包信息
    local control_file="$package_dir/DEBIAN/control"
    if [ ! -f "$control_file" ]; then
        echo "错误: 未找到 control 文件: $control_file"
        return 1
    fi
    
    local package_name version architecture
    package_name=$(grep -oP '(?<=Package: ).*' "$control_file")
    version=$(grep -oP '(?<=Version: ).*' "$control_file")
    architecture=$(grep -oP '(?<=Architecture: ).*' "$control_file")
    
    if [ -z "$package_name" ] || [ -z "$version" ] || [ -z "$architecture" ]; then
        echo "错误: 无法从 control 文件中提取包信息"
        return 1
    fi
    
    # 生成标准的 deb 文件名
    local deb_file="${output_dir}/${package_name}_${version}_${architecture}.deb"

    # 计算打包文件夹的大小（以KB为单位）
    local size_kb
    size_kb=$(du -sk "$package_dir" | cut -f1)
    
    # 修改 DEBIAN/control 文件中的 Installed-Size 值
    sed -i "s/^Installed-Size:.*/Installed-Size: $size_kb/" "$control_file"
    
    # 使用 dpkg-deb 打包成 deb 包
    dpkg-deb --build "$package_dir" "$deb_file"
    
    if [ $? -eq 0 ]; then
        echo "打包成功: $deb_file"
    else
        echo "打包失败"
        return 1
    fi
}
pack "$TMP_ROOT" "$SCRIPT_DIR"

