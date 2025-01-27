from django.shortcuts import render, redirect, HttpResponseRedirect, HttpResponse
from django.http import JsonResponse
from base.forms import LoginForm
from django.contrib import auth
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
import psutil
import re
import os, time, math
import datetime
import redis
import json
import pymysql, base64
import random
import numpy as np
import requests
import uuid
from multiprocessing import Pool
from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.schedulers.background import BackgroundScheduler
from kafka import KafkaProducer,KafkaConsumer
from kafka.errors import KafkaError
import pika
from kazoo.client import KazooClient
import pymongo

from base.ner.predict_span import predict,model_init
from base.spiders.event import get_event_yingjiju, get_event_bendibao, get_event_jiaoguanju, get_event_bus

from py2neo import Graph,Node,NodeMatcher
from base.GCN.LevelAnalysisModel import ATAnalysisModel
import base.GCN.XGBoostTree as XGBoostTree
from base.GCN.XGBoostTree import XGBoostRegressionTree
from base.GCN.DecisionTree import DecisionTree
from base.GCN.DecisionNode import DecisionNode


# Create your views here.
# bh_node_url = 'http://47.95.159.86:9999/'
bh_node_url = 'http://127.0.0.1:9999/'
# java_node_url = 'http://10.136.213.221:9999/'
# java_node_url = 'http://127.0.0.1:9999/'
database_host = '47.95.159.86'
database_name = 'TRAFFIC'
database_usrname = 'root'
database_password = '06240118'
kafka_server = '47.95.159.86:9092'
rabbitmq_host = '47.95.159.86'
redis_host = '47.95.159.86'
zk_host = '47.95.159.86'
mongo_host = '47.95.159.86'
mongo_dbname = 'TRAFFIC'
mongo_user = 'traffic'
mongo_password = '06240118'
neo4j_url = "http://47.95.159.86/:7474"
neo4j_graph = Graph(neo4j_url, auth=("neo4j", "06240118"))

TASKS = ["road_wks", "road_st", "road_at", "road_yq", "road_sg", "road_zjk", "weather"]

LEVEL_MAP = {1: 3, 2: 2, 3: 1}
node_tasks = {}
task_state = []
init_num = 0
event_switch = 0
nodeinfo = {}
registeNodes = []
traffic_Level = {}
area_data = {}
traffic_level_predict = {}

old_data_time = "2022-02-04 08:00"
tokenizer, label_list, model, device, id2label = model_init()

default_jobstore = MemoryJobStore()
default_executor = ThreadPoolExecutor(20)

predict_model_at = ATAnalysisModel()
predict_model_at.buildGraph(neo4j_graph)
predict_model_at.build_data()

init_scheduler_options = {
    "jobstores": {
        # first 为 jobstore的名字，在创建Job时直接直接此名字即可
        "default": default_jobstore
    },
    "executors": {
        # first 为 executor 的名字，在创建Job时直接直接此名字，执行时则会使用此executor执行
        "default": default_executor,
        'processpool': ProcessPoolExecutor(30)
    },
    # 创建job时的默认参数
    "job_defaults": {
        'coalesce': False,  # 是否合并执行
        'max_instances': 1  # 最大实例数
    }
}

scheduler = BackgroundScheduler(**init_scheduler_options)
scheduler.start()

road_dbs = ["bd_road_at", "bd_road_st", "bd_road_wks", "bd_road_yq", "bd_road_zjk", "bd_road_sg"]
eventGame_dbs = ["EVENT_at", "EVENT_st", "EVENT_wks", "EVENT_sg", "EVENT_yq", "EVENT_zjk",
                 "GAME_at", "GAME_st", "GAME_wks", "GAME_sg", "GAME_yq", "GAME_zjk",
                 ]

def saveData():
    client = pymongo.MongoClient( host=mongo_host,
                                  port=27017,
                                  username=mongo_user,
                                  password=mongo_password
                                  )
    mongo_db = client[mongo_dbname]

    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    for road_db in road_dbs:

        # SQL 查询语句
        sql = " SELECT * FROM " + road_db
        mongo_col = mongo_db[road_db]
        try:
            # 执行SQL语句
            cursor.execute(sql)
            # 获取所有记录列表
            results = cursor.fetchall()
            for data in results:
                condition = {'date': str(data[1]), 'road_name': data[2]}

                dic = {'date': str(data[1]), 'road_name': data[2], 'description': data[3], 'speed': data[4],
                       'congestion_distance': data[5], 'congestion_trend': data[6], 'section_desc': data[7]
                       }
                if mongo_col.find_one(condition) is None:
                    mongo_col.insert_one(dic)

        except:
            print("Error: unable to fetch data")

    for xdb in eventGame_dbs:
        game_sql = " SELECT * FROM " + xdb
        mongo_col = mongo_db[xdb]
        try:
            # 执行SQL语句
            cursor.execute(game_sql)
            # 获取所有记录列表
            results = cursor.fetchall()
            for data in results:
                dic = {'date': str(data[0]), 'content': data[2]}
                if mongo_col.find_one(dic) is None:
                    mongo_col.insert_one(dic)

        except:
            print("Error: unable to fetch data")

    db.close()

def health(nodename):
    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    dic = {}
    dic['node_name'] = nodename
    dic['info_name'] = "health"

    # producer.send(topic, json.dumps(dic).encode())
    msg = json.dumps(dic)
    channel = connection.channel()
    properties = pika.spec.BasicProperties(expiration="30000")
    try:
        channel.basic_publish(exchange='cloud-send',
                              routing_key=nodename,
                              body=msg,
                              properties=properties)

        flag = 1
        count = 0
        res = {}
        while flag:
            method_frame, header_frame, body = channel.basic_get(queue='edge-send-queue-' + nodename,
                                                                 auto_ack=False)
            if method_frame != None:
                res = json.loads(body)
                if res['id'] == "health":
                    channel.basic_ack(delivery_tag = method_frame.delivery_tag)
                    flag = 0
                else:
                    channel.basic_reject(delivery_tag = method_frame.delivery_tag)
                    count += 1
                    if count >= 800:
                        res["content"] = "暂无节点健康度"
                        return res["content"]
            else:
                count += 1
                if count >= 800:
                    res["content"] = "暂无节点健康度"
                    return res["content"]
    finally:
        connection.close()
    return res["content"]

level2used = {1: [20, 40], 2: [40, 60], 3: [60, 80]}
level2health = {1: [70, 100], 2: [60, 90], 3: [60, 80]}
is_opened = {"崇礼赛区节点": False, "延庆赛区节点": False, "北京城区节点": True}
def initAreaData():
    global area_data

    dict = {}
    dict['level'] = traffic_level_predict["road_zjk"]
    dict['isopened'] = is_opened['崇礼赛区节点']
    dict['event'] = "无"
    dict['used'] = random.randint(level2used[dict['level']][0], level2used[dict['level']][1])
    dict['health'] = random.randint(level2health[dict['level']][0], level2health[dict['level']][1])
    area_data['崇礼赛区节点'] = dict
    dict = {}
    dict['level'] = traffic_level_predict["road_yq"]
    dict['isopened'] = is_opened['延庆赛区节点']
    dict['event'] = "无"
    dict['used'] = random.randint(level2used[dict['level']][0], level2used[dict['level']][1])
    dict['health'] = random.randint(level2health[dict['level']][0], level2health[dict['level']][1])
    area_data['延庆赛区节点'] = dict
    dict = {}
    dict['level'] = max(traffic_level_predict["road_at"], traffic_level_predict["road_st"], traffic_level_predict["road_wks"])
    dict['isopened'] = is_opened['北京城区节点']
    dict['event'] = "无"
    dict['used'] = psutil.cpu_percent(interval=1)
    health_content = health("BUAA")
    if "节点健康评分为：" in health_content:
        pattern = re.compile(r'节点健康评分为：([0-9]+)')
        health_score = pattern.match(health_content).group(1)
    else:
        health_score = 0

    dict['health'] = health_score
    area_data['北京城区节点'] = dict


def updateAreaData():
    global area_data

    dict = {}
    dict['level'] = traffic_level_predict["road_zjk"]
    dict['isopened'] = is_opened['崇礼赛区节点']
    dict['event'] = "无"
    dict['used'] = random.randint(level2used[dict['level']][0], level2used[dict['level']][1])
    dict['health'] = random.randint(level2health[dict['level']][0], level2health[dict['level']][1])
    area_data['崇礼赛区节点'] = dict
    dict = {}
    dict['level'] = traffic_level_predict["road_yq"]
    dict['isopened'] = is_opened['延庆赛区节点']
    dict['event'] = "无"
    dict['used'] = random.randint(level2used[dict['level']][0], level2used[dict['level']][1])
    dict['health'] = random.randint(level2health[dict['level']][0], level2health[dict['level']][1])
    area_data['延庆赛区节点'] = dict
    dict = {}
    dict['level'] = max(traffic_level_predict["road_at"], traffic_level_predict["road_st"], traffic_level_predict["road_wks"])
    dict['isopened'] = is_opened['北京城区节点']
    dict['event'] = "无"
    dict['used'] = psutil.cpu_percent(interval=1)
    health_content = health("BUAA")
    if "节点健康评分为：" in health_content:
        pattern = re.compile(r'节点健康评分为：([0-9]+)')
        health_score = pattern.match(health_content).group(1)
    else:
        health_score = 0

    dict['health'] = health_score
    area_data['北京城区节点'] = dict


def initTrafficLevel():
    global traffic_Level
    traffic_Level['zjk'] = []
    dict = {}
    dict['name'] = "崇礼场馆群"
    dict['game'] = "跳台滑雪"
    dict['level'] = traffic_level_predict["road_zjk"]
    traffic_Level['zjk'].append(dict)

    traffic_Level['yq'] = []
    dict = {}
    dict['name'] = "国家高山滑雪中心"
    dict['game'] = "高山滑雪"
    dict['level'] = traffic_level_predict["road_yq"]
    traffic_Level['yq'].append(dict)
    dict = {}
    dict['name'] = "国家雪车雪橇中心"
    dict['game'] = "钢架雪车"
    dict['level'] = traffic_level_predict["road_yq"]
    traffic_Level['yq'].append(dict)

    traffic_Level['bh'] = []
    dict = {}
    dict['name'] = "五棵松体育馆"
    dict['game'] = "无"
    dict['level'] = traffic_level_predict["road_wks"]
    traffic_Level['bh'].append(dict)
    dict = {}
    dict['name'] = "首都体育馆"
    dict['game'] = "无"
    dict['level'] = traffic_level_predict["road_st"]
    traffic_Level['bh'].append(dict)
    dict = {}
    dict['name'] = "国家体育馆"
    dict['game'] = "无"
    dict['level'] = traffic_level_predict["road_at"]
    traffic_Level['bh'].append(dict)


def updateTrafficLevel():
    global traffic_Level
    traffic_Level['zjk'] = []
    dict = {}
    dict['name'] = "崇礼场馆群"
    dict['game'] = "跳台滑雪"
    dict['level'] = traffic_level_predict["road_zjk"]
    traffic_Level['zjk'].append(dict)

    traffic_Level['yq'] = []
    dict = {}
    dict['name'] = "国家高山滑雪中心"
    dict['game'] = "高山滑雪"
    dict['level'] = traffic_level_predict["road_yq"]
    traffic_Level['yq'].append(dict)
    dict = {}
    dict['name'] = "国家雪车雪橇中心"
    dict['game'] = "钢架雪车"
    dict['level'] = traffic_level_predict["road_yq"]
    traffic_Level['yq'].append(dict)

    traffic_Level['bh'] = []
    dict = {}
    dict['name'] = "五棵松体育馆"
    dict['game'] = "无"
    dict['level'] = traffic_level_predict["road_wks"]
    traffic_Level['bh'].append(dict)
    dict = {}
    dict['name'] = "首都体育馆"
    dict['game'] = "无"
    dict['level'] = traffic_level_predict["road_st"]
    traffic_Level['bh'].append(dict)
    dict = {}
    dict['name'] = "国家体育馆"
    dict['game'] = "无"
    dict['level'] = traffic_level_predict["road_at"]
    traffic_Level['bh'].append(dict)

def event_ner(request):


    input_text = "决定2020年8月12日至2020年9月10日期间，宫门口西岔(安平巷—阜成门内大街)采取禁止机动车由南向北方向行驶交通管理措施。"
    # inputs = ["冬奥会期间（2022年1月21日至2月25日），启用德胜门外大街（德胜门桥至马甸桥段）",
    #           "西北四环路（五棵松桥至四元桥段）",
    #           "德胜门外大街（德胜门桥至马甸桥段）",
    #           "京藏高速（马甸桥至健翔桥段）",
    #           "西直门外大街（西直门桥至中关村南大街段）",
    #           "紫竹院路（中关村南大街至车道沟桥段）",
    #           "科荟路（北辰东路至安立路段）",
    #           "北辰西路（北土城西路至科荟路段）",
    #           "中关村南大街（大慧寺路口至西直门外大街段）"]
    # for input_text in inputs:

    res = predict(input_text, tokenizer, label_list, model, device, id2label)

    # res = {"info": "未开启"}
    return JsonResponse(res, json_dumps_params={'ensure_ascii': False})

def getYingjiju(request):
    res = get_event_yingjiju()

    return JsonResponse(res, safe=False)


def getBendibao(request):
    res = get_event_bendibao()
    return JsonResponse(res, safe=False)


def getJiaoguanju(request):
    res = get_event_jiaoguanju()
    return JsonResponse(res, safe=False)


def getBus(request):
    res = get_event_bus()
    return JsonResponse(res, safe=False)

def job_execute(event):
    """
    监听事件处理
    :param event:
    :return:
    """
    print(
        "job执行job:\ncode => {}\njob.id => {}\njobstore=>{}".format(
            event.code,
            event.job_id,
            event.jobstore
        ))
    if event.job_id in TASKS:
        state = "数据融合"
        global task_state
        for i in range(0,len(task_state)):
            if task_state[i]['name'] == event.job_id:
                task_state[i]['state'] = state


def imgSave(imgName):
    pool = redis.ConnectionPool(host=redis_host, port=6379, password="06240118")  #配置连接池连接信息
    connect = redis.Redis(connection_pool=pool)

    ret = connect.get(imgName)

    img_data = base64.b64decode(ret)
    if os.path.exists("./static_files/img/resource-topo/"+imgName):
        return
    # 注意：如果是"data:image/jpg:base64,"，那你保存的就要以png格式，如果是"data:image/png:base64,"那你保存的时候就以jpg格式。
    with open("./static_files/img/resource-topo/"+imgName, 'wb') as f:
        f.write(img_data)


def traversebuild(nodeinfo,zk):
    Path = nodeinfo
    nodes = zk.get_children(Path)
    res = {}
    childList = []
    for node in nodes:
        if node == "children":
            children = zk.get_children(Path + "/" + node)

            for child in children:
                childList.append(traversebuild(Path + "/" + node + "/" + child,zk))

        else:
            value = zk.get(Path + "/"  + node)[0].decode('utf')
            res[node] = value
            if node == "img":
                imgSave(value)
    if len(childList) != 0:
        res['children'] = childList
    return res


def buildNodeInfo():
    zk = KazooClient(hosts=zk_host)
    zk.start()
    nodes = zk.get_children('/EdgeCloud')

    children = []
    global registeNodes
    global node_tasks
    for node in nodes:
        if not zk.exists('/EdgeCloud/' + node + "/" + "nodeinfo"):
            continue
        children.append(traversebuild('/EdgeCloud/' + node + "/" + "nodeinfo",zk))
        registeNodes.append(node)
        tasks = zk.get_children('/EdgeCloud/' + node + "/" + "images")
        node_tasks[node] = []
        for task in tasks:
            info = {}
            info['name'] = task
            info['id'] = zk.get('/EdgeCloud/' + node + "/" + "images" + "/"  + task)[0].decode('utf')
            node_tasks[node].append(info)





    with open("./static_files/data/resource-topo.json",'r') as load_f:
        load_dict = json.load(load_f)
    load_dict['children'] = children
    global nodeinfo
    nodeinfo = json.dumps(load_dict)
    buildTask()


def buildTask():
    # tasks = requests.get(java_node_url+'/rest/tasks')
    # tasks = json.loads(tasks.content)


    # credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    # connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    # dic = {}
    # node = "BUAA"
    # dic['node_name'] = "BUAA"
    # dic['info_name'] = "tasks"
    #
    # # producer.send(topic, json.dumps(dic).encode())
    # msg = json.dumps(dic)
    # channel = connection.channel()
    # try:
    #     channel.basic_publish(exchange='cloud-send',
    #                           routing_key=node,
    #                           body=msg)
    #
    #     flag = 1
    #
    #     while flag:
    #         method_frame, header_frame, body = channel.basic_get(queue='edge-send-queue-' + node,
    #                                                              auto_ack=False)
    #         if method_frame != None:
    #             res = json.loads(body)
    #             if res['id'] == "tasks":
    #                 channel.basic_ack(delivery_tag = method_frame.delivery_tag)
    #                 flag = 0
    #             else:
    #                 channel.basic_reject(delivery_tag = method_frame.delivery_tag)
    #
    # finally:
    #     connection.close()
    global node_tasks
    global task_state
    global traffic_level_predict
    for node_name in node_tasks:
        tasks = node_tasks[node_name]
        task_state = []
        for task in tasks:
            item = {}
            if task['name'] in TASKS:
                item['name'] = task['name']
                item['level'] = 1 #random.randint(1, 3)
                traffic_level_predict[task['name']] = item['level']
                item['state'] = "初始化"
                item['frequent'] = LEVEL_MAP[item['level']]
                item['edge_flow'] = 0
                item['cloud_flow'] = 0
                task_state.append(item)

        print(task_state)


def updateTask():
    global task_state
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT task_name,edge_flow,cloud_flow as cloud_sum FROM flow_table where to_days(time) = to_days(now());"
    res = {}
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            if data[0] in TASKS:
                if data[0] not in res:
                    res[data[0]] = [0, 0]
                res[data[0]][0] += data[1]
                res[data[0]][1] += data[2]
        for i in range(0,len(task_state)):
            if task_state[i]['name'] in res:
                task_state[i]['edge_flow'] = res[task_state[i]['name']][0]
                task_state[i]['cloud_flow'] = res[task_state[i]['name']][1]

    except:
        print("Error: unable to fetch data")
    db.close()




def taskSchedule2():

    global event_switch
    global scheduler
    global task_state
    global traffic_level_predict
    if event_switch == 1:
        state = "事件采集"

        i = 0

        for x in range(0, len(task_state)):
            if task_state[x]['name'] == "road_at":
                i = x
                break

        task_state[i]['state'] = state
        # 事件采集
        ex_task("BUAA", "event")


        state = "态势研判"
        task_state[i]['state'] = state
        #态势研判
        area_level_analysis()

        level = traffic_level_predict[task_state[i]['name']]
        if level != task_state[i]['level']:
            task_state[i]['level'] = level
            task_state[i]['frequent'] = LEVEL_MAP[level]
            if scheduler.get_job(task_state[i]['name'], "default"):
                # 存在的话，先删除
                scheduler.get_job(task_state[i]['name'], "default").pause()
                scheduler.remove_job(task_state[i]['name'], "default")

            scheduler.add_job(task_job, IntervalTrigger(minutes=LEVEL_MAP[level]), args=["BUAA", task_state[i]['name']], id=task_state[i]['name'], jobstore="default", executor="default")


def get_traffic_data_at():
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_at;"
    res = {}
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()

        for data in results:
            # print(data)
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2] + ':' + data[7], 'speed': data[4],
                   'section_id': data[9], 'direction': data[8]}
            res[data[2]] = data[4]
    except:
        print("Error: unable to fetch data")

    # 关闭数据库连接
    db.close()

    return res

def area_level_analysis():
    #交通态势
    global predict_model_at
    # at_road_data = get_traffic_data_at()
    at_road_data = {"京藏高速": random.randint(5, 40)}

    predict_model_at.update_data(at_road_data)

    at_road_level = predict_model_at.predict(predict_model_at.input_data[0])
    print("奥体周边路况数据:{}，交通态势:{}级".format(at_road_data, at_road_level))


    at_game_level = 1

    #事件提取
    pool = redis.ConnectionPool(host=redis_host, port=6379, password="06240118")  #配置连接池连接信息
    connect = redis.Redis(connection_pool=pool)
    at_event_level = 1
    for i in range(0, 17):
        time = []
        position = []
        address = []
        action = []
        dic = {"time":time, "position":position, "address":address, "action":action}
        ret = connect.get('event' + str(i))
        if ret == None:
            continue
        content = ret.decode("utf-8")
        res = re.split(r';|；|。', content)
        for text in res:
            if text != '':
                res = predict(text, tokenizer, label_list, model, device, id2label)
                if res != None:
                    for key in res['label']:
                        for item in res['label'][key].keys():
                            dic[key].append(item)
        for date in dic['time']:
            if '日' in date and '月' in date:
                pattern = re.compile(r'([0-9]+)月')
                month = pattern.search(date).group(1)
                pattern = re.compile(r'([0-9]+)日')
                day = pattern.search(date).group(1)
                time = datetime.datetime.strptime(str(datetime.datetime.now().year)+month+day, "%Y%m%d").date()
                now = datetime.datetime.now().date()

                time_dif = (now-time).days

                if time_dif < 0 or time_dif > 3:
                    continue

                if len(dic['address']) != 0:
                    query = "match p=((m:gym)-[*1..2]-(n:road)) where m.name='国家体育馆' and n.name='" + dic['address'][0] + "' return n,length(p);"
                    ans = neo4j_graph.run(query).data()
                    if len(ans) > 0:
                        skip = ans[0]["length(p)"]
                        road_level = ans[0]['n']['road_level']
                        current_event_level = 1
                        if skip <= 1 and road_level <= 3:
                            current_event_level = 3
                        else:
                            current_event_level = 2
                        at_event_level = max(at_event_level, current_event_level)



    at_traffic_level = round(at_road_level * 0.5 + at_event_level * 0.2 + at_game_level * 0.3)
    print("奥体周边路况拥堵等级：{}级，管制事件等级：{}级，场馆活动等级：{}级，总体交通态势：{}级".format(at_road_level, at_event_level, at_game_level, at_traffic_level))
    traffic_level_predict['road_at'] = at_traffic_level







def taskSchedule():
    global event_switch
    global scheduler
    global task_state
    if event_switch == 1:
        state = "事件采集"
        i = random.randint(0, 2)
        task_state[i]['state'] = state
        time.sleep(20)


        state = "态势研判"
        task_state[i]['state'] = state
        time.sleep(10)
        traffic_level_predict[task_state[i]['name']] = random.randint(1, 3)


        level = traffic_level_predict[task_state[i]['name']]
        if level != task_state[i]['level']:
            task_state[i]['level'] = level
            task_state[i]['frequent'] = LEVEL_MAP[level]
            if scheduler.get_job(task_state[i]['name'], "default"):
                # 存在的话，先删除
                scheduler.get_job(task_state[i]['name'], "default").pause()
                scheduler.remove_job(task_state[i]['name'], "default")

            scheduler.add_job(task_job, IntervalTrigger(minutes=LEVEL_MAP[level]), args=["BUAA", task_state[i]['name']], id=task_state[i]['name'], jobstore="default", executor="default")


def taskInit():
    global event_switch
    global init_num
    global task_state
    if event_switch == 1 and init_num == 0:
        for task in task_state:
            scheduler.add_job(task_job, IntervalTrigger(minutes=LEVEL_MAP[task['level']]), args=["BUAA", task['name']], id=task['name'], jobstore="default", executor="default")

        init_num = 1



buildNodeInfo()
initTrafficLevel()
initAreaData()
scheduler.add_listener(job_execute, EVENT_JOB_EXECUTED)
scheduler.add_job(updateTask, IntervalTrigger(seconds=30), id="updateTask", jobstore="default", executor="default")
scheduler.add_job(updateTrafficLevel, IntervalTrigger(seconds=30), id="updateTrafficLevel", jobstore="default", executor="default")
scheduler.add_job(saveData, IntervalTrigger(minutes=5), id="saveData", jobstore="default", executor="default")
scheduler.add_job(updateAreaData, IntervalTrigger(seconds=30), id="updateAreaData", jobstore="default", executor="default")
scheduler.add_job(buildNodeInfo, IntervalTrigger(seconds=300), id="buildNodeInfo", jobstore="default", executor="default")
scheduler.add_job(taskInit, IntervalTrigger(seconds=30), id="taskInit", jobstore="default", executor="default")
# scheduler.add_job(taskSchedule, IntervalTrigger(minutes=3), id="taskSchedule", jobstore="default", executor="default")

scheduler.add_job(taskSchedule2, IntervalTrigger(minutes=1), id="taskSchedule2", jobstore="default", executor="default")

def ex_task(node_name, task_name):
    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    try:

        dic = {"node_name": node_name, "task_name": task_name}

        msg = json.dumps(dic)
        channel = connection.channel()
        channel.basic_publish(exchange='auto-cloud-edge',
                              routing_key=node_name,
                              body=msg)


        flag = 1

        while flag:

            method_frame, header_frame, body = channel.basic_get(queue='auto-edge-cloud-queue-' + node_name,
                                                                 auto_ack=False)
            if method_frame != None:
                res = json.loads(body)
                if res['res'] == task_name:
                    channel.basic_ack(delivery_tag = method_frame.delivery_tag)
                    flag = 0
                else:
                    channel.basic_reject(delivery_tag = method_frame.delivery_tag)

    finally:
        connection.close()



def task_job(node_name, task_name):
    global event_switch
    if event_switch == 0:
        return
    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    state = "数据采集"
    global task_state
    for i in range(0,len(task_state)):
        if task_state[i]['name'] == task_name:

            task_state[i]['state'] = state
    try:

        dic = {"node_name": node_name, "task_name": task_name}

        msg = json.dumps(dic)
        channel = connection.channel()
        channel.basic_publish(exchange='auto-cloud-edge',
                                  routing_key=node_name,
                                  body=msg)


        flag = 1

        while flag:

            method_frame, header_frame, body = channel.basic_get(queue='auto-edge-cloud-queue-' + node_name,
                                                             auto_ack=False)
            if method_frame != None:
                res = json.loads(body)
                if res['res'] == task_name:
                    channel.basic_ack(delivery_tag = method_frame.delivery_tag)
                    flag = 0
                else:
                    channel.basic_reject(delivery_tag = method_frame.delivery_tag)

    finally:
        connection.close()




def eventDriving(request):
    ans = {'status': "off"}
    ans = json.dumps(ans)

    if request.method == "POST":
        global task_state
        global event_switch

        switch = request.POST['switch']
        if switch == "on":
            event_switch = 1

            ans = {'status': "on", 'task_state': task_state}
            ans = json.dumps(ans)
        elif switch == "off":
            event_switch = 0
            ans = {'status': "off"}
            ans = json.dumps(ans)
    return HttpResponse(ans)

def taskState(request):
    if event_switch == 0:
        ans = {"status": 0}
        ans = json.dumps(ans)

    elif event_switch == 1:
        ans = {"status": 1, "data": task_state}
        ans = json.dumps(ans)

    return HttpResponse(ans)

def login(request):
    message = ""
    form = LoginForm(request.POST or None)  # 获取登录表单样式
    if request.method == "POST":
        if form.is_valid():
            cd = form.cleaned_data
            input_name = cd['username']
            input_pwd = cd['password']
            url = request.POST['url']
            # print(url)
            user = authenticate(username=input_name, password=input_pwd)
            if user is not None and user.is_active:
                auth.login(request, user)
                return redirect('/')
            else:
                message = '用户名或密码不正确'
                print(message)

    return render(request, 'login.html', {'form': form, 'message': message})


def log_out(request):
    auth.logout(request)
    return HttpResponseRedirect("/login/")



def home(request):

    # return render(request, 'home_.html')
    return render(request, 'new_home.html', {"default_date": old_data_time})


def home_zjk(request):

    return render(request, 'home_zjk.html')


def home_st(request):

    return render(request, 'home_st.html')


def home_wks(request):

    return render(request, 'home_wks.html')


def home_at(request):

    return render(request, 'home_at.html')

def get_cpu_state(request):

    data = psutil.virtual_memory()
    total = data.total  # 总内存,单位为byte
    free = data.available  # 可用内存
    memory = (int(round(data.percent)))
    cpu = psutil.cpu_percent(interval=1)
    ret = [memory, cpu]
    js = json.dumps(ret)
    return HttpResponse(js)


def get_resource_monitor(request):

    return render(request, 'monitor.html')


def get_resource_topo(request):

    return render(request, 'resource-topo.html')


def compute_device_check(request, rid):
    return render(request, 'compute_device_check.html', {'rid': rid})


def get_road_info_yq(request):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_yq;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2]+':'+data[7], 'speed': data[4], 'section_id': data[9], 'direction': data[8]}
            res.append(dic)
    except:
        print("Error: unable to fetch data")

    event_sql = " SELECT * FROM EVENT_yq;"
    events = []
    try:
        # 执行SQL语句
        cursor.execute(event_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            events.append(dic)
    except:
        print("Error: unable to fetch data")

    game_sql = " SELECT * FROM GAME_yq;"
    games = []
    try:
        # 执行SQL语句
        cursor.execute(game_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            games.append(dic)
    except:
        print("Error: unable to fetch data")


    # 关闭数据库连接
    db.close()
    re = {'road': res, 'event': events, 'game': games}
    js = json.dumps(re)

    return HttpResponse(js)


def get_road_info_st(request):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_st;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2] + ':' + data[7], 'speed': data[4],
                   'section_id': data[9], 'direction': data[8]}
            res.append(dic)
    except:
        print("Error: unable to fetch data")

    event_sql = " SELECT * FROM EVENT_st;"
    events = []
    try:
        # 执行SQL语句
        cursor.execute(event_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            events.append(dic)
    except:
        print("Error: unable to fetch data")

    game_sql = " SELECT * FROM GAME_st;"
    games = []
    try:
        # 执行SQL语句
        cursor.execute(game_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            games.append(dic)
    except:
        print("Error: unable to fetch data")


    # 关闭数据库连接
    db.close()
    re = {'road': res, 'event': events, 'game': games}
    js = json.dumps(re)

    return HttpResponse(js)


def get_road_info_at(request):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_at;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()

        for data in results:
            # print(data)
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2] + ':' + data[7], 'speed': data[4],
                   'section_id': data[9], 'direction': data[8]}
            res.append(dic)
    except:
        print("Error: unable to fetch data")

    event_sql = " SELECT * FROM EVENT_at;"
    events = []
    try:
        # 执行SQL语句
        cursor.execute(event_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            events.append(dic)
    except:
        print("Error: unable to fetch data")

    game_sql = " SELECT * FROM GAME_at;"
    games = []
    try:
        # 执行SQL语句
        cursor.execute(game_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            games.append(dic)
    except:
        print("Error: unable to fetch data")


    # 关闭数据库连接
    db.close()
    re = {'road': res, 'event': events, 'game': games}
    js = json.dumps(re)

    return HttpResponse(js)


def get_road_info_wks(request):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_wks;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2] + ':' + data[7], 'speed': data[4],
                   'section_id': data[9], 'direction': data[8]}
            res.append(dic)
    except:
        print("Error: unable to fetch data")

    event_sql = " SELECT * FROM EVENT_wks;"
    events = []
    try:
        # 执行SQL语句
        cursor.execute(event_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            events.append(dic)
    except:
        print("Error: unable to fetch data")

    game_sql = " SELECT * FROM GAME_wks;"
    games = []
    try:
        # 执行SQL语句
        cursor.execute(game_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            games.append(dic)
    except:
        print("Error: unable to fetch data")

    # 关闭数据库连接
    db.close()
    re = {'road': res, 'event': events, 'game': games}
    js = json.dumps(re)

    return HttpResponse(js)


def get_road_info_sg(request):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_sg;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2] + ':' + data[7], 'speed': data[4],
                   'section_id': data[9], 'direction': data[8]}
            res.append(dic)
    except:
        print("Error: unable to fetch data")

    event_sql = " SELECT * FROM EVENT_sg;"
    events = []
    try:
        # 执行SQL语句
        cursor.execute(event_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            events.append(dic)
    except:
        print("Error: unable to fetch data")

    game_sql = " SELECT * FROM GAME_sg;"
    games = []
    try:
        # 执行SQL语句
        cursor.execute(game_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            games.append(dic)
    except:
        print("Error: unable to fetch data")

    # 关闭数据库连接
    db.close()
    re = {'road': res, 'event': events, 'game': games}
    js = json.dumps(re)

    return HttpResponse(js)


def get_road_info_zjk(request):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT * FROM bd_road_zjk;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[1]), 'road_name': data[2], 'text': data[2] + ':' + data[7], 'speed': data[4],
                   'section_id': data[9], 'direction': data[8]}
            res.append(dic)
    except:
        print("Error: unable to fetch data")

    event_sql = " SELECT * FROM EVENT_zjk;"
    events = []
    try:
        # 执行SQL语句
        cursor.execute(event_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            events.append(dic)
    except:
        print("Error: unable to fetch data")

    game_sql = " SELECT * FROM GAME_zjk;"
    games = []
    try:
        # 执行SQL语句
        cursor.execute(game_sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        for data in results:
            dic = {'date': str(data[0]), 'content': data[2]}
            games.append(dic)
    except:
        print("Error: unable to fetch data")

    # 关闭数据库连接
    db.close()
    re = {'road': res, 'event': events, 'game': games}
    js = json.dumps(re)

    return HttpResponse(js)


def trafficflow(request):
    return render(request, 'home_trafficflow.html')


task_list = [1, 2, 2, 2, 2, 2, 0]
task = [1, 1, 2, 2, 3, 4, 4]
flag = 0

def passenger_flow(request):
    data = psutil.virtual_memory()
    total = data.total  # 总内存,单位为byte
    free = data.available  # 可以内存
    memory = (int(round(data.percent)))
    cpu = psutil.cpu_percent(interval=1)

    data = {}
    global flag
    data['resource'] = [cpu+15, random.randint(10, 60), random.randint(10, 60)]
    data['label'] = task_list[flag]
    data['task'] = np.zeros(5).tolist()
    data['task'][task[flag]] = 1
    flag += 1
    if flag > 6:
        flag = 0
    print(data)
    js = json.dumps(data)

    return HttpResponse(js)


def query_resource(request):
    data = psutil.virtual_memory()
    total = data.total  # 总内存,单位为byte
    free = data.available  # 可以内存
    memory = (int(round(data.percent)))
    cpu = psutil.cpu_percent(interval=1)

    bh_data = requests.get(bh_node_url+"getCpuState")
    bh_data = json.loads(bh_data.content)
    bh_cpu = bh_data['cpu']
    bh_mem = bh_data['memory']

    data = {}
    data['resource'] = [cpu+10, float(bh_cpu)+10, random.randint(10, 60)]

    data['line'] = []
    data['line'].append(np.random.randint(2, 15, 24).tolist())
    data['line'].append(np.random.randint(1, 20, 24).tolist())
    js = json.dumps(data)

    return HttpResponse(js)


def data_flow(request):
    data = np.random.randint(200, 800, 1)
    js = json.dumps(data.tolist())

    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT sum(edge_flow) as edge_sum,sum(cloud_flow) as cloud_sum FROM flow_table;"
    res = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()

        res = []
        res.append(int(results[0][0]))
        js = json.dumps(res)

    except:
        print("Error: unable to fetch data")
    db.close()
    return HttpResponse(js)


def getRoadInfo(request):
    res = requests.get(bh_node_url+"getRoadInfo").content

    return HttpResponse(res)


def switchRoadInfo(request):
    res = requests.get(bh_node_url+"switchRoadInfo").content

    return HttpResponse(res)


def RoadInfoState(request):
    res = requests.get(bh_node_url+"roadinfoState").content

    return HttpResponse(res)


# 集散模式
def get_mode_analysis(request):

    return render(request, 'mode_analysis.html')

def get_mode_predict(request):

    return render(request, 'mode_predict.html')
def get_mid_mode_predict(request):

    return render(request, 'mid_mode_predict.html')
def get_small_mode_predict(request):

    return render(request, 'small_mode_predict.html')

def get_big_mode2_analysis(request):

    return render(request, 'big_mode2_analysis.html')
def get_big_mode3_analysis(request):

    return render(request, 'big_mode3_analysis.html')
def get_big_mode4_analysis(request):

    return render(request, 'big_mode4_analysis.html')

def get_mid_mode1_analysis(request):

    return render(request, 'mid_mode1_analysis.html')
def get_mid_mode2_analysis(request):

    return render(request, 'mid_mode2_analysis.html')
def get_mid_mode3_analysis(request):

    return render(request, 'mid_mode3_analysis.html')
def get_small_mode1_analysis(request):

    return render(request, 'small_mode1_analysis.html')
def get_small_mode2_analysis(request):

    return render(request, 'small_mode2_analysis.html')
def get_mid_mode1_analysis_wks(request):

    return render(request, 'mid_mode1_analysis_wks.html')
def get_mid_mode1_analysis_st(request):

    return render(request, 'mid_mode1_analysis_st.html')
def get_mid_mode2_analysis_wks(request):

    return render(request, 'mid_mode2_analysis_wks.html')
def get_mid_mode3_analysis_wks(request):

    return render(request, 'mid_mode3_analysis_wks.html')
def get_mid_mode_predict_st(request):

    return render(request, 'mid_mode_predict_st.html')
def get_mid_mode_predict_wks(request):

    return render(request, 'mid_mode_predict_wks.html')

def new_od_mode(request, name):

    name = '/' + name + '/'

    return render(request, 'od_mode.html', {"src": name})

def new_od_predict(request, name):

    name = '/' + name + '/'

    return render(request, 'od_predict.html', {"src": name})


def get_flow_data():
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()

    # SQL 查询语句
    sql = " SELECT DATE_FORMAT( `time`, \"%Y-%m-%d\" )  DATE,sum(edge_flow) AS edge,sum(cloud_flow) AS cloud FROM flow_table " \
          "GROUP BY DATE_FORMAT( time, \"%Y-%m-%d\" ) ORDER BY DATE_FORMAT( time, \"%Y-%m-%d\" ) DESC LIMIT 7;"
    js = []
    try:
        # 执行SQL语句
        cursor.execute(sql)
        # 获取所有记录列表
        results = cursor.fetchall()
        res = []
        for data in results:
            dic = {}
            dic['date'] = str(data[0])
            dic['edge'] = int(data[1])
            dic['cloud'] = int(data[2])
            res.append(dic)
        js = json.dumps(res)

    except:
        print("Error: unable to fetch data")
    db.close()
    return js


def toCloud(request):
    js = get_flow_data()
    return render(request, 'new_monitor.html', {"flow_data": js})


def toBHnode_monitor(request):
    js = get_flow_data()

    return render(request, 'BHnode_monitor.html', {"flow_data": js})

def toYQnode_monitor(request):
    js = get_flow_data()

    return render(request, 'YQnode_monitor.html', {"flow_data": js})

def toZJKnode_monitor(request):
    js = get_flow_data()

    return render(request, 'ZJKnode_monitor.html', {"flow_data": js})

def get_new_resource_topo(request):

    return render(request, 'new_resource-topo.html')


def toTopo(request):
    data = nodeinfo
    # data = json.dumps(data)
    return render(request, 'new_resource-topo.html', {"data": data})


def tonew_home(request):
    global old_data_time
    return render(request, 'new_home.html', {"default_date": old_data_time})


def new_home_at(request):
    return render(request, 'new_home_at.html', {"default_date": old_data_time})


def new_home_st(request):
    return render(request, 'new_home_st.html', {"default_date": old_data_time})


def new_home_wks(request):
    return render(request, 'new_home_wks.html', {"default_date": old_data_time})


def new_home_sg(request):
    return render(request, 'new_home_sg.html', {"default_date": old_data_time})


def new_home_zjk(request):
    return render(request, 'new_home_zjk.html', {"default_date": old_data_time})


tasks_map = {}


def getBH(request):
    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    dic = {}

    node = request.GET.get("nodename")

    dic['node_name'] = node
    dic['info_name'] = "tasks"

    # producer.send(topic, json.dumps(dic).encode())
    msg = json.dumps(dic)
    channel = connection.channel()
    try:
        channel.basic_publish(exchange='cloud-send',
                          routing_key=node,
                          body=msg)

        flag = 1

        while flag:
            method_frame, header_frame, body = channel.basic_get(queue='edge-send-queue-' + node,
                                                             auto_ack=False)
            if method_frame != None:
                res = json.loads(body)
                if res['id'] == "tasks":
                    channel.basic_ack(delivery_tag = method_frame.delivery_tag)
                    flag = 0
                else:
                    channel.basic_reject(delivery_tag = method_frame.delivery_tag)

    finally:
        connection.close()

    tasks = res["content"]

    # tasks = requests.get(java_node_url + '/rest/tasks')
    #
    # tasks = json.loads(tasks.content)


    task_name = []
    global tasks_map
    global event_switch
    if event_switch == 1:
        switch = "on"
    else:
        switch = "off"
    for task in tasks:

        task_name.append(task['name'])

    return render(request, 'bh_edgenode.html', {'tasks': task_name, 'switch': switch, 'nodename': node})

def toMap_test(request):

    return render(request, 'monitor_map.html')

def area_monitor(request):
    return render(request, 'area_monitor.html')

def area_topo(request):
    return render(request, 'area-topo.html')

def get_javaNode_sysInfo(request):
    # res = requests.get(java_node_url+'/rest/sysInfo')

    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))

    dic = {}
    node = request.GET.get("nodename")
    dic['node_name'] = node
    dic['info_name'] = "sysInfo"

    # producer.send(topic, json.dumps(dic).encode())
    msg = json.dumps(dic)
    channel = connection.channel()
    res = {}
    try:
        channel.basic_publish(exchange='cloud-send',
                              routing_key=node,
                              body=msg)

        flag = 1
        count = 0
        while flag:
            method_frame, header_frame, body = channel.basic_get(queue='edge-send-queue-' + node,
                                                                 auto_ack=False)
            if method_frame != None:
                res = json.loads(body)
                if res['id'] == "sysInfo":
                    channel.basic_ack(delivery_tag = method_frame.delivery_tag)
                    flag = 0
                else:
                    channel.basic_reject(delivery_tag = method_frame.delivery_tag)
                    count += 1
                    if count >= 500:
                        res["content"] = ""
                        return res["content"]
            else:
                count += 1
                if count >= 500:
                    res["content"] = ""
                    return res["content"]

    finally:
        connection.close()

    res = json.dumps(res["content"])

    return HttpResponse(res)

def start_task(request):
    res = ""
    # producer = KafkaProducer(bootstrap_servers=kafka_server)
    # consumer = KafkaConsumer('edge-cloud', group_id='cloud-edge-0', #auto_offset_reset='earliest',
    #                      bootstrap_servers=kafka_server)
    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))

    if request.method == 'POST':
        nodename = request.POST['nodename']
        print(nodename)
        topic = 'cloud-edge'
        name = request.POST['name']
        # input = request.POST['input']
        input = ''
        # res = request.POST['res']
        res = 'no'

        try:

            dic = {}
            dic['id'] = str(uuid.uuid4().hex)
            dic['time'] = datetime.datetime.now().strftime("%Y%m%d %H:%M:%S")
            dic['name'] = name
            dic['input'] = input
            dic['res'] = res

            # producer.send(topic, json.dumps(dic).encode())
            msg = json.dumps(dic)
            channel = connection.channel()
            channel.basic_publish(exchange='cloud-edge',
                                  routing_key=nodename,
                                  body=msg)
            print("发送数据：" + str(dic))
            flag = 1
        except KafkaError as e:
            print(e)



        try:

            while flag:

                # message = next(consumer)
                channel = connection.channel()
                method_frame = None
                count = 0
                while method_frame == None:
                    count += 1
                    method_frame, header_frame, body = channel.basic_get(queue='edge-cloud-queue-' + nodename,
                                            auto_ack=True)
                    if count >= 10000:
                        return HttpResponse("请求超时")

                res = json.loads(body)

                print("接收数据:" + str(res))
                flag = res['status']


        except KeyboardInterrupt as e:
            print(e)
        finally:
            pass

    connection.close()
    ans = res['info']
    if res['res'] != "":
        ans = "返回数据："
        ans += res['res']
    return HttpResponse(ans)


def healthWorker(nodename):
    credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    dic = {}
    dic['node_name'] = nodename
    dic['info_name'] = "health"

    # producer.send(topic, json.dumps(dic).encode())
    msg = json.dumps(dic)
    channel = connection.channel()
    properties = pika.spec.BasicProperties(expiration="30000")
    try:
        channel.basic_publish(exchange='cloud-send',
                              routing_key=nodename,
                              body=msg,
                              properties=properties)

        flag = 1
        count = 0
        res = {}
        while flag:
            method_frame, header_frame, body = channel.basic_get(queue='edge-send-queue-' + nodename,
                                                                 auto_ack=False)
            if method_frame != None:
                res = json.loads(body)
                if res['id'] == "health":
                    channel.basic_ack(delivery_tag = method_frame.delivery_tag)
                    flag = 0
                else:
                    channel.basic_reject(delivery_tag = method_frame.delivery_tag)
                    count += 1
                    if count >= 800:
                        res["content"] = "暂无节点健康度"
                        return res["content"]
            else:
                count += 1
                if count >= 800:
                    res["content"] = "暂无节点健康度"
                    return res["content"]
    finally:
        connection.close()
    return res["content"]



def getHealth(request):
    # res = requests.get(java_node_url+'/rest/health')

    # credentials = pika.PlainCredentials('root', '06240118')  # mq用户名和密码
    # # 虚拟队列需要指定参数 virtual_host，如果是默认的可以不填。
    # connection = pika.BlockingConnection(pika.ConnectionParameters(host = rabbitmq_host,port = 5672,virtual_host = '/',credentials = credentials))
    global registeNodes
    pool = Pool(processes=4)
    ans = {}
    for node in registeNodes:
        # dic = {}
        # dic['node_name'] = node
        # dic['info_name'] = "health"
        #
        # # producer.send(topic, json.dumps(dic).encode())
        # msg = json.dumps(dic)
        # channel = connection.channel()
        # try:
        #     channel.basic_publish(exchange='cloud-send',
        #                       routing_key=node,
        #                       body=msg)
        #
        #     flag = 1
        #
        #     while flag:
        #         method_frame, header_frame, body = channel.basic_get(queue='edge-send-queue-' + node,
        #                                                          auto_ack=False)
        #         if method_frame != None:
        #             res = json.loads(body)
        #             if res['id'] == "health":
        #                 channel.basic_ack(delivery_tag = method_frame.delivery_tag)
        #                 flag = 0
        #             else:
        #                 channel.basic_reject(delivery_tag = method_frame.delivery_tag)
        # finally:
        #     pass
        ans[node] = pool.apply_async(healthWorker, [node]).get()
    pool.terminate()
    pool.close()
    print(ans)
    # connection.close()
    ans = json.dumps(ans)
    return HttpResponse(ans)


def getBH_trafficLevel(request):
    global traffic_Level
    res = traffic_Level["bh"]

    res = sorted(res, key=lambda k: k['level'])
    ans = json.dumps(res)
    return HttpResponse(ans)

def getYQ_trafficLevel(request):
    global traffic_Level
    res = traffic_Level["yq"]


    res = sorted(res, key=lambda k: k['level'])
    ans = json.dumps(res)
    return HttpResponse(ans)


def getZJK_trafficLevel(request):
    global traffic_Level
    res = traffic_Level["zjk"]


    res = sorted(res, key=lambda k: k['level'])
    ans = json.dumps(res)
    return HttpResponse(ans)


def refchangfeng(request):

    return redirect("http://121.89.204.250:9090/")


def datashow(request):

    return redirect("http://121.89.204.250:9090/#/dashboard")


def apidata(request):

    return redirect("http://121.89.204.250:9090/#/dataCollection/timedTask")


def filedata(request):

    return redirect("http://121.89.204.250:9090/#/dataCollection/ontTimeTask")


def usermanage(request):

    return redirect("http://121.89.204.250:9090/#/dataMonitoring/authority/userList")


def projectmanage(request):

    return redirect("http://121.89.204.250:9090/#/dataMonitoring/project/list")


def imagemanage(request):

    return redirect("http://121.89.204.250:9090/#/dataMonitoring/mirrorList/list")


def dockermanage(request):

    return redirect("http://121.89.204.250:9090/#/dataMonitoring/container/list")


def programmamanage(request):

    return redirect("http://121.89.204.250:9090/#/dataMonitoring/program/list")


def taskmanage(request):

    return redirect("http://121.89.204.250:9090/#/dataMonitoring/job/timedTask2")

def nodemanage(request):

    return redirect("http://121.89.204.250:9090/#/cloudEdgeCollaboration/list")

def toposhow(request):

    return redirect("http://121.89.204.250:9090/#/cloudEdgeCollaboration/Topology")

def timeapi(request):

    return redirect("http://121.89.204.250:9090/#/dataAccess/timedTask")

def onceapi(request):

    return redirect("http://121.89.204.250:9090/#/dataAccess/outTimeTask")

def test(request):

    return render(request, 'lyear_js_datepicker.html')


def writeWithDate(org_date):
    global old_data_time
    global event_switch
    if event_switch == 1:
        return False

    date = org_date.replace(' ', '_').replace(':', '_').replace('-', '_')
    file_name = date + '.json'
    old_dir = "./static_files/old_data"
    for dirpath, dirnames, filenames in os.walk(old_dir):
        if file_name in filenames:
            with open(os.path.join(old_dir,file_name), "r") as f:
                load_dict = json.load(f)
                for tb_name in load_dict:
                    data_type = load_dict[tb_name]['type']
                    datas = load_dict[tb_name]['data']
                    if data_type == "road_info":
                        insertRodaInfo(tb_name, datas)
                    elif data_type == "game_event":
                        insertEventGame(tb_name, datas)
            old_data_time = org_date
            return True

        else:
            return False



def writeOldData(request):
    org_date = request.GET.get('date')
    res = writeWithDate(org_date)
    ans = {"success": res}
    return HttpResponse(ans)





def excSQL(db,sql):
    cursor = db.cursor()
    cursor.execute(sql)
    db.commit()


def insertRodaInfo(tb_name, datas):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()
    # 清除原数据
    sql1 = "truncate table " + tb_name
    excSQL(db, sql1)
    # SQL 插入语句

    sql = "INSERT INTO " + tb_name + " (time,road_name,description,speed,congestion_distance,congestion_trend,section_desc,direction,section_id)" \
          "VALUES (%s, %s,  %s,  %s,  %s,%s,%s,%s,%s)" \
          "ON DUPLICATE KEY UPDATE time=%s,road_name=%s,description=%s,speed=%s,congestion_distance=%s," \
          "congestion_trend=%s,section_desc=%s,direction=%s,section_id=%s;"

    for res in datas:
        try:
            # 执行sql语句
            excSQL(db, "set names utf8;")
            cursor.execute(sql,(res['time'],res['road_name'],res['description'],res['speed'],res['congestion_distance'],res['congestion_trend'],res['section_desc'],res['direction'],res['section_id'],res['time'],res['road_name'],res['description'],res['speed'],res['congestion_distance'],res['congestion_trend'],res['section_desc'],res['direction'],res['section_id']))
            # 提交到数据库执行
            db.commit()



        except pymysql.Error as e:
            print(res['time'],res['road_name'],res['description'],res['speed'],res['congestion_distance'],res['section_desc'])
        # 如果发生错误则回滚
            db.rollback()

    # 关闭数据库连接
    db.close()



def insertEventGame(tb_name, datas):
    # 打开数据库连接
    db = pymysql.connect(host=database_host,
                         database=database_name,
                         port=3306,
                         user=database_usrname,
                         password=database_password,
                         charset="utf8",
                         use_unicode=True)

    # 使用cursor()方法获取操作游标
    cursor = db.cursor()
    # 清除原数据
    sql1 = "truncate table " + tb_name
    excSQL(db, sql1)
    # SQL 插入语句

    sql = "INSERT INTO " + tb_name + " (date,source,content)" \
                                     "VALUES (%s, %s,%s)" \
                                     "ON DUPLICATE KEY UPDATE date=%s,source=%s,content=%s;"

    for res in datas:
        try:
            # 执行sql语句
            excSQL(db, "set names utf8;")
            cursor.execute(sql,(res['date'], res['source'], res['content'], res['date'], res['source'], res['content']))
            # 提交到数据库执行
            db.commit()



        except pymysql.Error as e:
            print(res['date'], res['source'], res['content'])
            # 如果发生错误则回滚
            db.rollback()

    # 关闭数据库连接
    db.close()


writeWithDate(old_data_time)


def get_area_data(request):
    global area_data
    res = area_data

    ans = json.dumps(res)
    return HttpResponse(ans)