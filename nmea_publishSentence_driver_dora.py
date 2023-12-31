from typing import Callable
from dora import DoraStatus
from DoraNmeaDriver_utils import DoraNMEADriver
import re
import calendar
import math
import time
import pyarrow as pa
import pickle

"""
完成了对应ros2的NavSatFix、QuaternionStamped、TwistStamped速度部分的消息发布
输出：第一个字节表示消息类型： 1 表示NavSatFix消息类型
                          2 表示QuaternionStamped消息类型
                          3 表示TwistStamped消息类型
"""
def safe_float(field):
    try:
        return float(field)
    except ValueError:
        return float('NaN')

def safe_int(field):
    try:
        return int(field)
    except ValueError:
        return 0


#将度分格式转换为十进制格式
def convert_latitude(field):
    return safe_float(field[0:2]) + safe_float(field[2:]) / 60.0

#将度分格式转换为十进制格式
def convert_longitude(field):
    return safe_float(field[0:3]) + safe_float(field[3:]) / 60.0

#读取年月日和时间转化为时间戳
def convert_time(nmea_utc):
    # Get current time in UTC for date information
    utc_struct = time.gmtime()  # immutable, so cannot modify this one
    utc_list = list(utc_struct)
    # If one of the time fields is empty, return NaN seconds
    if not nmea_utc[0:2] or not nmea_utc[2:4] or not nmea_utc[4:6]:
        return float('NaN')
    else:
        hours = int(nmea_utc[0:2])
        minutes = int(nmea_utc[2:4])
        seconds = int(nmea_utc[4:6])
        utc_list[3] = hours
        utc_list[4] = minutes
        utc_list[5] = seconds
        unix_time = calendar.timegm(tuple(utc_list))
        return unix_time


def convert_status_flag(status_flag):
    if status_flag == "A":
        return True
    elif status_flag == "V":
        return False
    else:
        return False


def convert_knots_to_mps(knots):
    return safe_float(knots) * 0.514444444444


# 角度转换为弧度，需要这个包装函数是因为math.radians函数不会自动转换输入。
def convert_deg_to_rads(degs):
    return math.radians(safe_float(degs))


# 解析句子所用的字典和对应的操作
"""Format for this dictionary is a sentence identifier (e.g. "GGA") as the key, with a
list of tuples where each tuple is a field name, conversion function and index
into the split sentence"""
parse_maps = {
    "GGA": [
        ("fix_type", int, 6),                 #定位质量指示，表示定位的类型和精度
        ("latitude", convert_latitude, 2),    #纬度
        ("latitude_direction", str, 3),
        ("longitude", convert_longitude, 4),  #经度
        ("longitude_direction", str, 5),
        ("altitude", safe_float, 9),          #海拔高度
        ("mean_sea_level", safe_float, 11),
        ("hdop", safe_float, 8),              #水平精度因子
        ("num_satellites", safe_int, 7),
        ("utc_time", convert_time, 1),
    ],
    "RMC": [
        ("utc_time", convert_time, 1),
        ("fix_valid", convert_status_flag, 2),      #定位状态，通常为'A'表示定位有效，'V'表示定位无效
        ("latitude", convert_latitude, 3),
        ("latitude_direction", str, 4),
        ("longitude", convert_longitude, 5),
        ("longitude_direction", str, 6),
        ("speed", convert_knots_to_mps, 7),         #地面速率
        ("true_course", convert_deg_to_rads, 8),    #地面航向
    ],
    "GST": [
        ("utc_time", convert_time, 1),
        ("ranges_std_dev", safe_float, 2),
        ("semi_major_ellipse_std_dev", safe_float, 3),  #主要轴方向的标准偏差
        ("semi_minor_ellipse_std_dev", safe_float, 4),  #次要轴方向的标准偏差
        ("semi_major_orientation", safe_float, 5),      #主轴与正北方向的方向偏差
        ("lat_std_dev", safe_float, 6),  #高度方向的伪距噪声偏差
        ("lon_std_dev", safe_float, 7),  #经度方向的伪距噪声偏差
        ("alt_std_dev", safe_float, 8),  #纬度方向的伪距噪声偏差
    ],
    "HDT": [
        ("heading", safe_float, 1),
    ],
    "VTG": [
        ("true_course", safe_float, 1),
        ("speed", convert_knots_to_mps, 5)   #速度
    ]
}

#检查传入的NMEA句子是否符合特定的格式，然后解析NMEA句子的各字段
def parse_nmea_sentence(nmea_sentence):
    #检查传入的NMEA句子是否符合特定的格式
    if not re.match(r'(^\$GP|^\$GN|^\$GL|^\$IN).*\*[0-9A-Fa-f]{2}$', nmea_sentence):
        print("Regex didn't match, sentence not valid NMEA? Sentence was: %s"
              % repr(nmea_sentence))
        return False
    fields = [field.strip(',') for field in nmea_sentence.split(',')]

    # Ignore the $ and talker ID portions (e.g. GP)
    sentence_type = fields[0][3:]
    # 解析NMEA句子的各字段
    if sentence_type not in parse_maps:
        print("Sentence type %s not in parse map, ignoring."
              % repr(sentence_type))
        return False

    parse_map = parse_maps[sentence_type]
    parsed_sentence = {}
    for entry in parse_map:
        parsed_sentence[entry[0]] = entry[1](fields[entry[2]])

    # 测试输出
    # for key, value in parsed_sentence.items():
    #     print(f"Key: {key}, Value: {value}")
    return {sentence_type: parsed_sentence}

 
class Operator:
    """
    QuaternionStamped、QuaternionStamped和TwistStamped中速度消息的发布
    """

    def __init__(self):
        self.driver = DoraNMEADriver()


    def on_event(
        self,
        dora_event: dict,
        send_output: Callable[[str, bytes], None],
    ) -> DoraStatus:
        if dora_event["type"] == "INPUT":

            return self.on_input(dora_event, send_output)
        return DoraStatus.CONTINUE

    def on_input(
        self,
        dora_input: dict,
        send_output: Callable[[str, bytes], None],
    ):

        if "nmea_sentence" == dora_input["id"]:

            dora_original_input = dora_input["value"]
            dora_input = pa.array(dora_original_input, type=pa.uint8())
            # 使用utf-8编码将字节解码为字符串
            nmea_sentence = bytes(dora_input.to_pylist()).decode("utf-8")
            print(nmea_sentence)

            frame_id = self.driver.get_frame_id()
            #按类型解析NMEA数据:return解析的消息类型、解析得到map(对应消息类型各字段的key-value)
            parsed_sentence = parse_nmea_sentence(nmea_sentence)

            if not parsed_sentence:
                print("Failed to parse NMEA sentence. Sentence was: %s" % nmea_sentence)
                return DoraStatus.CONTINUE

            #nmea_Publish_sentence_type消息类型标志位
            # 按类型解析NMEA数据 包装成类，便于后续序列化send_output
            nmea_Publish_sentence_type , nmea_Publish_instance = self.driver.publish_parsed_sentence(parsed_sentence, frame_id)

            if nmea_Publish_sentence_type == 0:
                return DoraStatus.CONTINUE

            # 解析到fix消息类型或者heading消息类型或者vel消息类型
            else:
                bytes_type = nmea_Publish_sentence_type.to_bytes(1, byteorder='big')
                parsed_nmea_sentence = pickle.dumps(nmea_Publish_instance)
                parsed_nmea_sentence = bytes_type + parsed_nmea_sentence

            send_output(
                "parsed_nmea_sentence",
                parsed_nmea_sentence,
                #dora_input["metadata"],
            )
            return DoraStatus.CONTINUE


        else :
            return DoraStatus.CONTINUE
