#!/usr/bin/env python
# coding: utf-8

# # COVID-19 All Related articles (and tagged relation) tracker

# 
# First we take all Wikidata Articles that links to a main  COVID-19 pages [COVID-19 (Q84263196)](https://www.wikidata.org/wiki/Q84263196) ;  SARS-CoV-2 (Q82069695) and   [2019–20 COVID-19 pandemic (Q81068910)](https://www.wikidata.org/wiki/Q81068910), and then we join both sets and do a final pass to get relationships between the seeds and all the resulting items.


#Note that the output is saved on SQLite database
#Results are optimized for DB

import requests
from SPARQLWrapper import SPARQLWrapper, JSON, SPARQLExceptions
import pandas as pd
import sqlite3
from tqdm import tqdm
from functools import reduce, partial
from itertools import chain
from urllib.error import HTTPError

#get crawling timestamp
now  = pd.Timestamp.now()
sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
#I've added props, to get sitelinks/urls that is not coming by default
wikidata_query_base = 'https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=aliases|claims|datatype|descriptions|info|labels|sitelinks|sitelinks/urls&ids=' 

def get_SPARQL_results(query):
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    try:
        result = sparql.query()
        
        if result.response.status == 200:
            return result.convert()
        else:
            print(result.response.msg)

    except (HTTPError, SPARQLExceptions.EndPointInternalError) as e:
        print(e)
        print(query)
        return None

# pages linking COVID-19 wikidata item Q84263196
# whatlinks approach
def get_whatlinks(itemid):
    whatLinks = []
    url = f"https://www.wikidata.org/w/api.php?action=query&format=json&list=backlinks&bltitle={itemid}&bllimit=500&blnamespace=0"
    response = requests.get(url=url).json()
    whatLinks.extend(response['query']['backlinks'])

    while 'continue' in response:
        url = url + '&blcontinue='+ response['continue']['blcontinue']
        response = requests.get(url=url).json()
        whatLinks.extend(response['query']['backlinks'])

    QswhatLinks = (v['title'] for v in whatLinks)
    QswhatLinks = set(QswhatLinks)
    return(Qswhatlinks)

#Complementary approach using SPARQL
#TODO: Double check if there are pages appearing that were not included in the whatlinks approach
def get_all_statements(itemid):
    #https://w.wiki/KvX (Thanks User:Dipsacus_fullonum)
    # All statements with item, property, value and rank with COVID-19 (Q84263196) as value for qualifier.
    results = get_SPARQL_results(f"""
    SELECT ?item ?itemLabel ?property ?propertyLabel ?value ?valueLabel ?rank ?qualifier ?qualifierLabel
    WHERE
    {{
      ?item ?claim ?statement.
      ?property wikibase:claim ?claim.
      ?property wikibase:statementProperty ?sprop.
      ?statement ?sprop ?value.
      ?statement wikibase:rank ?rank. 
      ?statement ?qprop wd:{itemid}. # COVID-19


      ?qualifier wikibase:qualifier ?qprop.
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
    }}
    """)

    return get_Qs(results)

# All truthy statements with COVID-19 (Q84263196) as value.
#https://w.wiki/KvZ (Thanks User:Dipsacus_fullonum)
def get_truthy_statements(itemid):
    results = get_SQARQL_results(f"""
    SELECT ?item ?itemLabel ?property ?propertyLabel
    WHERE
    {{
      ?item ?claim wd:{itemid}.
      ?property wikibase:directClaim ?claim.
       SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
    }}""")

    return get_Qs(results)

def get_statements(itemid):

    sources = [get_truthy_statements,
               get_all_statements,
               get_whatlinks]

    return(reduce(set.union, (src(itemid) for src in sources),set()))

def get_Qs(results):
    if results is None:
        return []

    urls = pd.json_normalize(results['results']['bindings'])

    if len(urls) > 0:
        urlQ = [ link.split('/')[-1] for link in urls['item.value'].tolist()]
        return urlQ
    else:
        return []


def get_items_for_wp_articles(titles, lang='en'):
    langtitles = ' '.join([f"\"{title}\"@{lang}" for title in titles])
    results = get_SPARQL_results(f"""
    SELECT ?item 
    WHERE
    {{
      ?article schema:about ?item.
      ?article schema:name ?title.
    VALUES ?title {{ {langtitles} }}
    }}
    """)

    return get_Qs(results)

def get_statements_for_ids(item_ids):
    found_Qs = reduce(set.union, map(get_all_statements, tqdm(item_ids)),set())
    return found_Qs.union(set(item_ids))

### Getting articles and relation

#Def aux function to divide list on chunks
def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def get_item_infos(items):
    url = wikidata_query_base  + '|'.join(items)
    return requests.get(url=url).json().get('entities',dict())

#Define parsing functions
def getRelationships(claims,targetQs): #TODO change relationship to relation
    '''
    This function receives a list of claims from a Wikidata Item, and a list of target Qs
    Iterating over the claims, looking for the target Qs and returning the pair Property and target Q
    For example, if it find relationship Part of (P31) of Q12323 (that is the target list)
    will return [(P31,Q3)]
    inputs:
    claims: object, result from wikidata queries like 
            'https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&ids=Q5' 
    targetQs: list of str, where str are Q values 
    output:
        return a list of pairs (prop,target)
    '''
    pairs = []
    for prop, relationships in claims.items():
        for relationship in relationships:
            if 'mainsnak' in relationship:
                datatype = relationship['mainsnak'].get('datatype','')
                if datatype=='wikibase-item':
                    try: #found some cases without  id even for a wikibase-item datatype
                        Qfound = relationship['mainsnak']['datavalue']['value'].get('id','')
                        if Qfound in targetQs:
                            pairs.append([prop,targetQs[targetQs.index(Qfound)]])
                    except:
                        pass
    if not pairs:
        pairs.append(['unknown','unknown'])
    return pairs

def getValueIfWikidataItem(claim):
    '''
    this function return a list of values for a given claim, if those values point to a wikidata item
    datatype=='wikibase-item'
    input:
    claim: object
    output:
    wikidataItems: list of str
      '''
    output = []
    for relationship in claim:
        if 'mainsnak' in relationship:
            datatype = relationship['mainsnak'].get('datatype','')
            if datatype=='wikibase-item':
                Qfound = relationship['mainsnak']['datavalue']['value'].get('id','')
                output.append(Qfound)
    if not output:
        output.append('unknown')
    return output
    

if __name__=="__main__":
    import argparse
    import sys
    print('test')
    parser = argparse.ArgumentParser(description='Crawl wikidata to find related WP articles.')
    parser.add_argument('-i', '--base-ids', nargs = '+', help='list of wikidata ids to initialize crawl', default=['Q81068910','Q84263196','Q82069695'])

    parser.add_argument('-a', '--articles-file',  help='file containing list of wikipedia article http urls to initialize crawl', type = argparse.FileType('r'), default=[])

    args = parser.parse_args()
    print(args)
    article_names = map(str.strip, args.articles_file)
    print(list(article_names))

    article_chunks = list(chunks(list(article_names), 50))
    print('test2')
    print("getting ids from articles")
    ids_from_articles = [id for id in chain(* map(get_items_for_wp_articles, tqdm(article_chunks)))]

    print(ids_from_articles)

    ids = set(args.base_ids + ids_from_articles)

    print("getting statements for ids")
    Qs = get_statements_for_ids(ids)

    chunked_Qs = list(chunks(list(Qs),50))

    print("fetching item infos")

    # {**d1,**d2} evaluates to an updated dictionary with values from d2 replacing those from d1.
    # tqdm is an easy lightweight progress bar
    itemsInfo = reduce(lambda d1,d2: {**d1,**d2}, map(get_item_infos, tqdm(chunked_Qs)), dict())
    ## Parse result and build pandas dataframes
    pagesPerProject = {}
    pagesPerProjectTable = {}
    itemsInfoTable = {}
    labelsEn = {}
    for item,v in itemsInfo.items():
        itemsInfoTable[item] = {}
        try:
            itemsInfoTable[item]['item_Label'] = v['labels']['en']['value']
        except:
            itemsInfoTable[item]['item_Label'] = 'unknown '
        #checking if there are claims for that Q, if not claims we return an empty dict, to avoid errors
        claims = v.get('claims',{})
        if 'P31' in  claims: #getting part of to classify the item        
            itemsInfoTable[item]['Instace_Of'] = getValueIfWikidataItem(claims.get('P31'))
        else:
            itemsInfoTable[item]['Instace_Of'] = ['unknown']
        #find COVID-19 / COVID-19 pandemics relationships
        itemsInfoTable[item]['RelationTuple'] = getRelationships(claims,['Q81068910','Q84263196'])

        if 'sitelinks' in v:
            for wiki,data in v['sitelinks'].items():
                page = data['title']
                project ='%s.%s' %  (data['url'][8:].split('.')[0],data['url'][8:].split('.')[1]) #could be more elegant with regex           
                pagesPerProject[project] = pagesPerProject.get(project,[])
                pagesPerProject[project].append(page)
                article_link  = data['url']
                if project.split('.')[1] == 'wikipedia' or  project.split('.')[0] == 'commons': #iwlinks : https://meta.wikimedia.org/wiki/Help:Interwiki_linking
                    projectcode = project.split('.')[0]
                else:
                    projectcode = '%s:%s ' % (project.split('.')[1],project.split('.')[0])
                wikilink = '[[%s:%s|%s]]' % (projectcode,page,page)
                pagesPerProjectTable[article_link] = {'project':project,'page':page,'wikidataItem':item,'wikilink':wikilink}

    #Build     itemsInfoTable        
    itemsInfoTable = pd.DataFrame.from_dict(itemsInfoTable,orient='index')
    itemsInfoTable['last_seen'] = now
    itemsInfoTable = itemsInfoTable.explode('Instace_Of').explode('RelationTuple') 
    itemsInfoTable['connector'] = itemsInfoTable['RelationTuple'].apply(lambda x:x[0])
    itemsInfoTable['connected_To'] = itemsInfoTable['RelationTuple'].apply(lambda x:x[1])
    itemsInfoTable.drop('RelationTuple',inplace=True,axis=1)

    #
    pagesPerProjectTable = pd.DataFrame.from_dict(pagesPerProjectTable,orient='index')
    pagesPerProjectTable['last_seen'] = now
    pagesPerProjectTable['url'] = pagesPerProjectTable.index

    connectedToLabel = {'Q84263196':'COVID-19', 'Q81068910':'2019–20 COVID-19 pandemic'} 
    itemsInfoTable['connected_To_Label'] = itemsInfoTable['connected_To'].apply(lambda x:connectedToLabel.get(x))

    ## Getting labels for connector (properties)
    Ps = list(itemsInfoTable['connector'].unique())
    props = []
    print('getting props labels')
    for P in Ps:
        props.append(requests.get('https://www.wikidata.org/w/api.php?action=wbgetentities&ids=%s&format=json' % P).json())
    propLabels ={}
    for P in tqdm(props):
        if 'entities' in P:
            for Pid,data in P['entities'].items():
                tmplabel = data.get('labels').get('en',{})
                propLabels[Pid]= tmplabel.get('value','unknown')
    propLabels = pd.DataFrame.from_dict(propLabels,orient='index',columns=['connector_Label'])
    propLabels['connector'] = propLabels.index

    #adding labels to itemsInfoTable
    itemsInfoTable = itemsInfoTable.join(propLabels, on='connector',rsuffix='_tmp').drop('connector_tmp',axis=1)
    itemsInfoTable['item_id'] = itemsInfoTable.index


    ## Getting Instance of labels
    instaceOfQs = list(itemsInfoTable['Instace_Of'].unique())
    print(len(instaceOfQs))
    QiOf = [] # Q instace
    for Q in tqdm(instaceOfQs):
        QiOf.append(requests.get('https://www.wikidata.org/w/api.php?action=wbgetentities&ids=%s&format=json' % Q).json())
    QiOfLabels ={}
    for P in QiOf:
        if 'entities' in P:
            for Pid,data in P['entities'].items():
                tmplabel = data.get('labels').get('en',{})
                QiOfLabels[Pid]= tmplabel.get('value','unknown')
    QiOfLabels = pd.DataFrame.from_dict(QiOfLabels,orient='index',columns=['Instace_Of_Label'])
    QiOfLabels['Instace_Of'] = QiOfLabels.index

    #FINALVERSION Of Info Table
    itemsInfoTable = itemsInfoTable.join(QiOfLabels, on='Instace_Of',rsuffix='_tmp').drop('Instace_Of_tmp',axis=1)


    # SAve on database
    conn = sqlite3.connect('./AllWikidataItems.sqlite')
    # first 
    itemsInfoTable.to_sql(name='itemsInfoTable', if_exists='replace', con=conn)
    pagesPerProjectTable.to_sql(name='pagesPerProjectTable', if_exists='replace', con=conn)
