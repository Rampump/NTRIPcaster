

# 2RTK NTRIP Caster  
**这是一个使用python编写的NTRIP caster程序，它可以使用web管理挂载点和用户管理功能，支持NTRIP V1.0/V2.0协议。******  


## 🚀 核心功能  
### 一、NTRIP协议全流程支持 

- **基准站数据接入**：支持多基准站通过NTRIP协议上传RTK原始数据（如RTCM3格式）等gnss数据  
- **用户数据分发**：实时向所有用户推送挂载点的差分数据  
- **挂载点管理**：灵活创建/编辑/删除挂载点，配置数据来源与权限  
- **用户认证体系**：用户名/密码认证. 单个用户可以同时建立3个连接.从同一个或不同挂载点拉取数据.兼容 NTRIP V1.0  V2.0 协议.

### 二、可视化Web管理后台  
| 功能模块         | 核心操作                                                                 |  
|------------------|--------------------------------------------------------------------------|  
| **用户管理**     | 创建/修改用户                                           |  
| **挂载点配置**   | 配置挂载点名称。设置单独上传密码：当前支持NTRIP V1.0协议上传               |  
| **系统监控**     | 查看服务运行状态、内存/CPU占用率、当前连接数                              |  
| **安全设置**     | 修改管理员密码、                           |  


## 📦 系统要求  
| 组件                | 配置建议                          |  
|---------------------|---------------------------------------|  
| 操作系统            | Debian 12 (Bookworm)              |  
| Python运行环境      | Python 3.9+ (Debian 12默认版本)       |  
| 网络端口            | 2101/TCP（NTRIP服务）、5757/TCP（web管理界面） |  
| 硬件推荐            | 2核CPU / 2GB内存 / 10GB存储空间（单机部署） | 
| 其他架构           | 支持x86_64和ARM架构                |  


## 🚀 快速部署指南  
### 一键自动化安装（推荐）  
```bash  
# 克隆项目仓库  
git clone https://gitcode.com/rampump/NTRIPcaster.git && cd NTRIPcaster  地址1
git clone https://github.com/Rampump/NTRIPcaster.git && cd NTRIPcaster   地址2

# 赋予安装脚本执行权限并运行  
sudo chmod +x install.sh  
sudo ./install.sh  
```  
**自动完成操作**：  
✅ 安装系统依赖（Python3、Pip、Git等）  
✅ 创建独立服务用户`2rtk`与数据目录  
✅ 安装Python依赖包（基于`requirements.txt`）  
✅ 配置Systemd服务并启动  
✅ 自动开放防火墙端口（UFW环境）云服务器需在安全界面开启5757和2101端口  

### 手动分步安装  
#### 1. 基础环境准备  
```bash  
# 更新系统并安装必备工具  
sudo apt update && sudo apt install -y python3 python3-pip git  

# 创建服务用户与数据目录  
sudo useradd -r -s /bin/false 2rtk  
sudo mkdir -p /opt/2rtk/{config,data} /var/log/2rtk  
```  

#### 2. 项目文件部署  
```bash  
# 复制项目文件到指定目录  
sudo cp -r ./* /opt/2rtk/  

# 配置文件权限（关键！）  
sudo chown -R 2rtk:2rtk /opt/2rtk /var/log/2rtk  
sudo chmod -R 755 /opt/2rtk /var/log/2rtk  
```  

#### 3. 依赖安装与服务启动  
```bash  
# 安装Python依赖  
sudo pip3 install -r /opt/2rtk/requirements.txt  

# 部署Systemd服务  
sudo cp 2rtk.service /etc/systemd/system/  
sudo systemctl daemon-reload && sudo systemctl enable --now 2rtk  
```  


## ⚙️ 服务管理命令  
| 操作类型         | 命令示例                                  |  
|------------------|-------------------------------------------|  
| 启动服务         | `sudo systemctl start 2rtk`                |  
| 停止服务         | `sudo systemctl stop 2rtk`                 |  
| 重启服务         | `sudo systemctl restart 2rtk`              |  
| 查看运行状态     | `sudo systemctl status 2rtk -l`            |  
| 查看实时日志     | `sudo journalctl -u 2rtk -f`               |  
| 配置开机自启     | `sudo systemctl enable 2rtk`               |  



### 1. 端口与防火墙  

 阿里云腾讯云等云服务器搭建请在安全界面 开启2101和5757端口
- **NTRIP服务端口**：2101（TCP协议，用于基准站数据上传与用户订阅）  
- **Web管理端口**：5757（TCP协议，建议通过Nginx反代并启用HTTPS） 

- **自建服务器防火墙配置**（UFW示例）：  
  ```bash  
  sudo ufw allow 2101/tcp && sudo ufw allow 5757/tcp  
  ```  

### 2. Web管理界面  
- 访问地址：`http://服务器IP:5757`  
- **默认账号**：  
  ✅ 用户名：`admin`  
  ⚠️ 初始密码：`admin`（**首次登录后必须立即修改！**）  


### 1. 服务启动失败  
```bash  
# 查看系统日志定位问题  
sudo journalctl -u 2rtk --since "10min ago"  

# 检查端口占用情况  
sudo lsof -i :2101 || sudo lsof -i :5757  
```  

### 2. 数据传输异常  
- 基准站无法连接：  
  ✔ 确认NTRIP客户端配置正确（服务器IP、端口、挂载点名称）  
  ✔ 检查`/opt/2rtk/config/routers.conf`中挂载点数据源配置  
- 用户收不到数据：  
  ✔ 验证用户认证信息是否正确（查看Web后台用户列表）  
  ✔ 检查挂载点是否启用“数据转发”开关  




- **联系方式**：  
  ✉️ 邮箱：`i@jia.by`  



## 📄 许可证  
本项目采用 **MIT License**，允许商业使用、修改和再发布，但需保留原作者声明。  

