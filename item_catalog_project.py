from flask import Flask, render_template, make_response, request, flash, redirect, url_for, jsonify
from werkzeug.routing import BaseConverter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database_entities import User, Category, Item, Base

from flask import session as login_session
import random
import string

from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
import requests

app = Flask(__name__)


class RegexConverter(BaseConverter):

    def __init__(self, url_map, *items):
        super(RegexConverter, self).__init__(url_map)
        self.regex = items[0]

app.url_map.converters['regex'] = RegexConverter


CLIENT_ID = json.loads(
    open('client_secret.json', 'r').read())['web']['client_id']

engine = create_engine('sqlite:///itemcatalogwithcategory.db')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


@app.route('/googleconnect', methods=['POST'])
def googleConnect():

    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secret.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])

    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps('Current user is already connected.'),
                                 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['credentials'] = credentials
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    login_session['provider'] = 'google'

    user_id = get_user_by_email(data['email'])
    if not user_id:
        user_id = create_user(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
    flash("you are now logged in as %s" % login_session['username'])
    print "done!"
    return output


@app.route('/gdisconnect')
def gdisconnect():
    # Only disconnect a connected user.
    credentials = login_session.get('credentials')
    if credentials is None:
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    access_token = credentials.access_token
    url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]
    if result['status'] != '200':
        # For whatever reason, the given token was invalid.
        response = make_response(
            json.dumps('Failed to revoke token for given user.'), 400)
        response.headers['Content-Type'] = 'application/json'
        return response


@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
            del login_session['credentials']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully been logged out.")
        return redirect('/')
    else:
        flash("You were not logged in")
        return redirect('/')


@app.route('/login')
def login():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    return render_template('login.html', STATE=state)


@app.route("/create/<regex('\D+'):param>", methods=['POST', 'GET'])
def create_post(param):
    """
    Method name: create_post
    Description:
        This method is responsible to create an item or give the user the page that contains the form to create them.
    Args:
        param (data type: str): It is the category user wants to create an item.
    Returns:
        If request.method == get it should return template to create an item
        else it should save the recently created item in database and then return to category's page
        in both case the user must be logged in.
    """
    if request.method == 'GET':
        if 'user_id' in login_session:
            return render_template('item_create.html', cat=param)
        else:
            flash("You don't have permission to create a post.")
            return redirect(url_for('listing_category', param=param))

    if request.method == 'POST':

        if 'user_id' in login_session:
            name = request.form['nameItem']
            description = request.form['descriptionItem']
            user = login_session['user_id']
            cat = get_category_id(param)
            print cat

            new_item = Item(name=name, description=description,
                            user_id=user, cat=cat)
            session.add(new_item)
            session.commit()

            return redirect('/%s' % param)

        else:
            flash("You don't have permission to create an item.")
            return redirect(url_for('listing_category', param=param))


@app.route("/resume/<regex('\d+'):param>")
def resume_item(param):
    """
    Method name: resume_item
    Description:
        This method is responsible to show an item if it exists, else it will show an error message.
    Args:
        param (data type: int): It is the id of the item user wants to retrieve.
    Returns:
        return the item if it was found on the database,
        else just return an error message
    """
    item_id = param
    try:
        item = session.query(Item).filter_by(item_id=item_id).one()
        return render_template('resume_item.html', item=item)
    except:
        flash("Item doesn't exist")
        return redirect('/')


@app.route("/resume/<regex('\d+'):param>/JSON", methods=['GET'])
def resume_item_json(param):
    """
    Method name: resume_item_json
    Description:
        Same as method resume_item but instead it will return the found item in json format
    """
    item = session.query(Item).filter_by(item_id=param).first()
    if item is not None:
        return jsonify(item_id=item.item_id, name=item.name,
                       description=item.description, user_id=item.user_id,
                       category_id=item.category_id)
    else:
        return jsonify(Item=[])


@app.route("/remove/<regex('\d+'):param>")
def remove_item(param):
    """
    Method name: remove_item
    Description:
        This method is responsible to remove an item if it exists, else it will show an error message.
    Args:
        param (data type: int): It is the id of the item user wants to retrieve.
    Returns:
        delete item in database if it exists.
        it should first check if user is logged in, if positive then should check if the user logged is the same user that created the item,
        and then finally delete the item
    """
    if 'user_id' in login_session:
        item_selected = session.query(Item).filter_by(item_id=param).one()
        category = session.query(Category).filter_by(
            category_id=item_selected.category_id).one()
        if login_session['user_id'] == item_selected.user_id:
            session.query(Item).filter_by(item_id=param).delete()
            session.commit()
            flash("Item successfully deleted!.")
        else:
            flash("You don't own this item. You cannot delete it")

        return redirect('/%s' % category.category)
    else:
        flash("You don't have permission to remove an item.")
        return redirect('/resume/%s' % param)


@app.route("/edit/<regex('\d+'):param>", methods=['GET', 'POST'])
def edit_item(param):
    """
    Method name: edit_item
    Description:
        This method is responsible to edit an item if it exists, else it will show an error message.
    Args:
        param (data type: int): It is the id of the item user wants to retrieve.
    Returns:
        edit item in database if it exists.
        it should first check if user is logged in, if positive then should check if the user logged is the same user that created the item,
        and then finally edit the item
    """
    if 'user_id' in login_session:
        item_selected = session.query(Item).filter_by(item_id=param).one()
        if login_session['user_id'] == item_selected.user_id:

            category = session.query(Category).filter_by(
                category_id=item_selected.category_id).one()

            if request.method == 'POST':
                new_name = request.form['nameItem']
                new_description = request.form['descriptionItem']
                item_selected.name = new_name
                item_selected.description = new_description
                session.add(item_selected)
                session.commit()
                flash("Item successfully edited!.")
            else:
                return render_template('item_create.html',
                                       old_name=item_selected.name,
                                       old_description=item_selected.description, id=param)
        else:
            flash("You don't own this item. You cannot edit it")
    else:
        flash("You don't have permission to edit an item.")

    return redirect('/resume/%s' % param)


@app.route("/<regex('\D+'):param>", methods=['GET'])
def listing_category(param):
    """
    Method name: listing_category
    Description:
        This method is responsible to list all item in categories in database.
    Args:
        param (data type: str): category name user wants to retrieve.
    Returns:
        first we need to check if category exists, if true, then list all item inside that category.
        finally it should return a list with all item, else just return an error message
    """
    category_exists = session.query(Category).filter_by(category=param).first()
    items_category = []
    if category_exists:
        items_category = session.query(Item).filter_by(
            category_id=category_exists.category_id).all()
    else:
        flash("This category doesn't exist!", 'error')

    if category_exists and items_category is not None:
        if len(items_category) == 0:
            flash("There aren't any items for this category!", 'error')

    return render_template('items_category.html', items=items_category, cat=param)


@app.route('/')
def main_page():
    all_categories = session.query(Category).all()
    return render_template('main.html', categories=all_categories)


def get_user_by_email(email):
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.user_id
    except:
        return None


def get_category_id(category):
    try:
        cat = session.query(Category).filter_by(category=category).first()
        return cat
    except:
        return None


def create_user(login_session):
    new_user = User(name=login_session['username'],
                    email=login_session['email'], picture=login_session['picture'])
    session.add(new_user)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.user_id


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = False
    app.run(host='0.0.0.0', port=5000)
