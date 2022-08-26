import connectorx as cx
import psycopg2
import time
import os
import numpy as np
import math
from sklearn.metrics import classification_report, mean_squared_error
import treelite_runtime

def calculate_time(start_time,end_time):
    diff = (end_time-start_time)*1000
    return diff

def load_data_from_pickle(dataset):
    raise NotImplementedError

def fetch_data(dataset, config, suffix, time_consume=None):
    if dataset == "covtype":
        load_data_from_pickle(dataset)
    try:
        pgsqlconfig = config["pgsqlconfig"]
        datasetconfig = config[dataset]
        query = datasetconfig["query"]+"_"+suffix
        dbURL = "postgresql://"+pgsqlconfig["username"]+":"+pgsqlconfig["password"]+"@"+pgsqlconfig["host"]+":"+pgsqlconfig["port"]+"/"+pgsqlconfig["dbname"]
        print(dbURL)
        print(query)
        start_time = time.time()
        dataframe = cx.read_sql(dbURL, query)
        end_time = time.time()
        data_loading_time = calculate_time(start_time,end_time)
        if time_consume is not None:
            time_consume["data loading time"] = data_loading_time
        print(f"Time Taken to load {dataset} as a dataframe is: {data_loading_time}")

        if datasetconfig["type"] == "classification":
            dataframe = dataframe.astype({datasetconfig["y_col"]: int})
        return dataframe
    except psycopg2.Error as e:
        print("Postgres Database error: " + e + "/n")

# def get_data(data, size=-1):
#     np_data = data.to_numpy() if not isinstance(data, np.ndarray) else data
#     if size != -1:
#         np_data = np_data[0:size, :]
#     return np_data

def convert_to_hummingbird_model(model, backend, test_data, batch_size, device):
    from hummingbird.ml import constants
    from hummingbird.ml import convert, convert_batch
    remainder_size = test_data.shape[0] % batch_size 
    extra_config = {constants.N_THREADS: os.cpu_count()}
    batch_data = None
    batch_data = test_data[0:batch_size]
    if backend == "tvm":
        # single_batch = np.array(test_data[0:batch_size], dtype=np.float32)
        # batch_data = np.array(single_batch, dtype=np.float32)
        model = convert(model, backend, batch_data, device=device, extra_config=extra_config)
    else:
        # batch_data = get_data(test_data, batch_size)
        model = convert_batch(model, backend, batch_data, remainder_size, device=device, extra_config=extra_config)
    return model

def run_inference(framework, features, input_size, query_size, predict, time_consume):
    start_time = time.time()
    results = []
    iterations = math.ceil(input_size/query_size)
    if framework == "TreeLite":
        def aggregate_function():
            def append(output):
                results.append(output)
            def extend(output):
                results.extend(output)
            return append if query_size == 1 else extend

        aggregate_func = aggregate_function()
        for i in range(iterations):
            query_data = treelite_runtime.DMatrix(features[i*query_size:(i+1)*query_size])
            output = predict(query_data)
            output = np.where(output > 0.5, 1, 0)
            aggregate_func(output)
    elif framework == "TFDF":
        for i in range(iterations):
            query_data = features[i*query_size:(i+1)*query_size]
            output = predict(query_data).flatten()
            output = np.where(output > 0.5, 1, 0)
            results.extend(output)
    elif framework == "HummingbirdTVMCPU":
        for i in range(iterations):
            query_data = features[i*query_size:(i+1)*query_size]
            output = predict(query_data, len(query_data)!=query_size)
            results.extend(output)
    else:
        for i in range(iterations):
            query_data = features[i*query_size:(i+1)*query_size]
            output = predict(query_data)
            results.extend(output)

    inference_time = calculate_time(start_time, time.time())
    time_consume["inference time"] = inference_time
    print(f"Time Taken to predict on {framework} is {inference_time}")
    return results

def write_data(framework, results, time_consume):
    start_time = time.time()
    # arr = np.array(results)
    # df = pd.DataFrame(arr)
    # df.to_csv(os.path.join('results','results.txt'), index=False) 
    #print(results[0:10])
    with open(os.path.join('results','results.txt'), 'w') as f:
        for item in results:
            f.write("%s\n" % item)
    
    writing_time = calculate_time(start_time, time.time())
    time_consume["result writing time"] = writing_time
    print(f"Time Taken to write results to a text file for {framework} is {writing_time}")

def find_accuracy(framework,y_actual, y_pred):
    print("Classification Report", framework)
    print(classification_report(y_actual,y_pred))
    print("################")

def find_MSE(framework,y_actual, y_pred):
    print("Regression Report", framework)
    print(f"MSE: {mean_squared_error(y_actual, y_pred)}")
    print("################")

def relative2abspath(path, *paths):
    return os.path.join(
        os.path.dirname(__file__),
        path,
        *paths
    )

def check_argument_conflicts(args):
    model = args.model.lower()
    frameworks = args.frameworks.lower().split(",")
    dataset = args.dataset.lower()
    if "treelite" in frameworks and model == "randomforest":
        raise ValueError("TreeLite models only supports xgboost algorithm, but does not support randomforest algorithm.")
    if dataset == "bosch" and model == "randomforest":
        raise ValueError("Sklearn implementation of randomforest algorithm does not support datasets with missing values.")
    if "lleaves" in frameworks and not model == "lightgbm":
        raise ValueError("LLeaves Framework supports compilation of LightGBM Models.")