import socket
import sys

def send_test_packet(ip, port):
    """
    向隐匿隧道的入口节点发送一个测试 UDP 数据包
    """
    # 创建一个 UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_address = (ip, int(port))
    message = b"HELLO-SDN-OBFUSCATION-TEST"

    try:
        print(f"[*] 准备向首跳节点 {ip}:{port} 发送隐匿探测包...")
        sent = sock.sendto(message, server_address)
        
        # 设置 2 秒超时，尝试接收回包（虽然 8.8.8.8:53 可能不会回这个字符串，但我们要观察流量）
        sock.settimeout(2.0)
        print(f"[+] 数据包已发出 ({sent} bytes)，请检查 agent.py 的后台流量日志。")
        
    except Exception as e:
        print(f"[-] 发包失败: {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python smr_probe.py <首跳IP> <随机端口>")
    else:
        send_test_packet(sys.argv[1], sys.argv[2])